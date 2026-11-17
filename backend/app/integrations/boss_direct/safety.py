"""JobPilot 自身的互斥和保守批次预算；不读写 BossHunter 账本。"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

from app.integrations.collector import CollectorError


@contextmanager
def collector_lease(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "collector.lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise CollectorError("busy", "已有 JobPilot 实例正在读取此浏览器，请等待结束。") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class BatchBudget:
    def __init__(self, root: Path, limit: int = 20):
        self.root, self.path, self.default_limit = root, root / "daily_batches.json", limit
        self.settings_path = root / 'reading_settings.json'

    @staticmethod
    def now():
        return datetime.now(timezone(timedelta(hours=8)))

    @property
    def limit(self) -> int:
        try:
            if not self.settings_path.exists():
                return self.default_limit
            settings = json.loads(self.settings_path.read_text(encoding='utf-8'))
            limit = settings['daily_limit']
            if type(limit) is not int or limit < 1:
                raise ValueError('invalid daily limit')
            return limit
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise CollectorError('budget_unreadable', '读取额度设置无法确认，已停止；不会回退设置或清空计数。') from exc

    def status(self) -> dict:
        """只读本地设置和计数；跨日查询也不改写账本，不访问Chrome。"""
        now = self.now()
        today = now.date().isoformat()
        limit = self.limit
        try:
            if self.path.exists():
                ledger = json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(ledger, dict) or type(ledger.get('count')) is not int or ledger['count'] < 0:
                    raise ValueError('invalid ledger')
                ledger_date = datetime.strptime(ledger['date'], '%Y-%m-%d').date()
                if ledger_date > now.date():
                    raise ValueError('future ledger')
                used = ledger['count'] if ledger_date == now.date() else 0
            else:
                used = 0
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise CollectorError('budget_unreadable', 'JobPilot 本地读取记录无法确认，已停止，不自动重置。') from exc
        reset_at = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return {'date': today, 'timezone': 'Asia/Shanghai', 'daily_limit': limit,
                'used': used, 'remaining': max(0, limit-used), 'exhausted': used >= limit,
                'reset_at': reset_at.isoformat(),
                'limit_source': 'local_settings' if self.settings_path.exists() else 'environment_default'}

    def set_limit(self, limit: int) -> dict:
        if type(limit) is not int or limit < 1:
            raise ValueError('每日上限必须是正整数')
        # 与读取共用同一互斥锁，防止其他实例在任务执行中修改上限。
        with collector_lease(self.root):
            self.status()  # 记录损坏时不通过修改上限掩盖错误。
            staged = self.settings_path.with_suffix('.tmp')
            staged.write_text(json.dumps({'daily_limit': limit}), encoding='utf-8')
            staged.replace(self.settings_path)
            return self.status()

    def reserve(self):
        status = self.status()
        if status['exhausted']:
            raise CollectorError('quota_exhausted', f"JobPilot 今日读取额度已用完（上限{status['daily_limit']}次，已用{status['used']}次，剩余0次，含列表、刷新及自动JD）；保留已取得的结果。")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        staged = self.path.with_suffix(".tmp")
        staged.write_text(json.dumps({"date": status['date'], "count": status['used'] + 1}), encoding="utf-8")
        staged.replace(self.path)
