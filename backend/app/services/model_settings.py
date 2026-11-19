"""本机模型配置；密钥仅在后端存储，响应不包含密钥内容。"""
import json
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.core.config import Settings


class ModelSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    base_url: str = Field(max_length=1000)
    model: str = Field(default='', max_length=200)
    api_key: SecretStr | None = None
    clear_api_key: bool = False

    @field_validator('base_url')
    @classmethod
    def valid_url(cls, value):
        value=value.strip().rstrip('/')
        if value:
            u=urlsplit(value)
            if u.scheme not in {'http','https'} or not u.hostname or u.username or u.password or u.query or u.fragment or any(c.isspace() for c in value):
                raise ValueError('请输入不含凭证、查询参数或片段的 HTTP(S) Base URL')
            _=u.port
        return value

    @field_validator('model')
    @classmethod
    def valid_model(cls,value):
        if any(ord(c)<32 for c in value):raise ValueError('模型名称不允许控制字符')
        return value.strip()

    @model_validator(mode='after')
    def valid_key_action(self):
        key=self.api_key.get_secret_value() if self.api_key else ''
        if self.clear_api_key and key:raise ValueError('不能同时设置和清除 Key')
        if len(key)>8192 or any(c.isspace() for c in key):raise ValueError('Key 不允许空白字符且长度不得超过8192')
        return self


class ModelSettingsStore:
    def __init__(self,path:Path,defaults:Settings):
        self.path,self.defaults,self.lock=path,defaults,Lock()

    def _read(self):
        try:
            local=self.path.exists()
            data=json.loads(self.path.read_text(encoding='utf-8')) if local else {
                'base_url':self.defaults.ai_base_url,'model':self.defaults.ai_model,'api_key':self.defaults.ai_api_key}
            checked=ModelSettingsUpdate.model_validate(data)
            return {'base_url':checked.base_url,'model':checked.model,
                    'api_key':checked.api_key.get_secret_value() if checked.api_key else '', 'source':'local_settings' if local else 'environment'}
        except (ValueError,OSError,TypeError):
            raise RuntimeError('无法读取模型配置，请检查本地配置文件；未覆盖原文件。') from None

    @staticmethod
    def public(data):
        return {'base_url':data['base_url'],'model':data['model'],'has_api_key':bool(data['api_key']),
                'configured':bool(data['base_url'] and data['model'] and data['api_key']),
                'source':data['source']}

    def get(self):
        with self.lock:return self.public(self._read())

    def credentials(self):
        """仅供后端调用方使用；不注册为HTTP响应。"""
        with self.lock:return self._read().copy()

    def update(self,body:ModelSettingsUpdate):
        with self.lock:
            previous=self._read()
            key=body.api_key.get_secret_value() if body.api_key else ''
            data={'base_url':body.base_url,'model':body.model,
                  'api_key':'' if body.clear_api_key else key or previous['api_key']}
            self.path.parent.mkdir(parents=True,exist_ok=True)
            staged=self.path.with_suffix('.tmp')
            try:
                with staged.open('w',encoding='utf-8') as f:
                    json.dump(data,f,ensure_ascii=False,indent=2)
                staged.replace(self.path)
            except OSError:
                raise RuntimeError('模型配置保存失败，未确认保存成功。') from None
            return self.public({**data,'source':'local_settings'})
