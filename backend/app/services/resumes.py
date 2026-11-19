"""本机简历版本库：只检查格式和保存原件，不解析正文或调用AI。"""
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4
import hashlib
import zipfile

import olefile
from sqlalchemy import Column, Integer, String, create_engine, select
from sqlalchemy.orm import declarative_base, Session

MAX_FILE_SIZE=10*1024*1024
MIME={'.pdf':'application/pdf','.doc':'application/msword',
      '.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}
Base=declarative_base()


class ResumeVersion(Base):
    __tablename__='resume_versions'
    __table_args__={'sqlite_autoincrement':True}
    version=Column(Integer,primary_key=True,autoincrement=True)
    id=Column(String,nullable=False,unique=True)
    filename=Column(String,nullable=False)
    extension=Column(String,nullable=False)
    size_bytes=Column(Integer,nullable=False)
    uploaded_at=Column(String,nullable=False)
    sha256=Column(String,nullable=False)


def validate_document(filename:str,data:bytes):
    name=filename.replace('\\','/').rsplit('/',1)[-1].strip()
    if not name or len(name)>200 or any(ord(c)<32 for c in name):raise ValueError('文件名无效或过长')
    ext=Path(name).suffix.lower()
    if ext not in MIME:raise ValueError('仅支持 PDF、DOC 和 DOCX 格式')
    if not data:raise ValueError('不能上传空文件')
    if len(data)>MAX_FILE_SIZE:raise ValueError('文件不能超过10MB')
    if ext=='.pdf':
        valid=data.startswith(b'%PDF-') and b'%%EOF' in data[-2048:]
    elif ext=='.doc':
        try:
            with olefile.OleFileIO(BytesIO(data)) as doc:
                valid=doc.exists('WordDocument') and (doc.exists('0Table') or doc.exists('1Table'))
        except (OSError,ValueError):valid=False
    else:
        try:
            with zipfile.ZipFile(BytesIO(data)) as doc:
                entries=doc.infolist()
                valid=(len(entries)<=2048 and sum(e.file_size for e in entries)<=50*1024*1024
                       and not any(e.flag_bits&1 or e.filename.lower().endswith('vbaproject.bin') for e in entries)
                       and {'[Content_Types].xml','word/document.xml'}.issubset(doc.namelist()))
                if valid:
                    types=doc.getinfo('[Content_Types].xml')
                    valid=types.file_size<=1024*1024 and b'wordprocessingml.document.main+xml' in doc.read(types)
        except (OSError,ValueError,zipfile.BadZipFile,RuntimeError):valid=False
    if not valid:raise ValueError('文件内容与格式不符，或文档已加密/损坏；请重新导出PDF或DOCX')
    return name,ext


class ResumeStore:
    def __init__(self,root:Path):
        self.root=root.resolve()
        self.files=self.root/'files'
        self.files.mkdir(parents=True,exist_ok=True)
        self.engine=create_engine('sqlite:///'+str(self.root/'versions.db'),connect_args={'check_same_thread':False,'timeout':15})
        Base.metadata.create_all(self.engine)

    @staticmethod
    def public(row):
        return {k:getattr(row,k) for k in ('id','version','filename','extension','size_bytes','uploaded_at','sha256')} | {
            'status':'uploaded','parsed':False,'download_url':f'/resumes/{row.id}/download'}

    def save(self,filename,data):
        name,ext=validate_document(filename,data)
        ident=str(uuid4());target=self.files/(ident+ext);staged=target.with_suffix('.tmp')
        row=ResumeVersion(id=ident,filename=name,extension=ext,size_bytes=len(data),
                          uploaded_at=datetime.now(timezone.utc).isoformat(),sha256=hashlib.sha256(data).hexdigest())
        try:
            staged.write_bytes(data);staged.replace(target)
            with Session(self.engine,expire_on_commit=False) as session:
                session.add(row);session.commit()
                return self.public(row)
        except Exception:
            # 仅清理本次UUID对应的新文件，不覆盖或删除历史版本。
            target.unlink(missing_ok=True)
            raise
        finally:staged.unlink(missing_ok=True)

    def list(self):
        with Session(self.engine) as session:
            return [self.public(row) for row in session.scalars(select(ResumeVersion).order_by(ResumeVersion.version.desc()))]

    def download(self,ident):
        with Session(self.engine) as session:
            row=session.scalar(select(ResumeVersion).where(ResumeVersion.id==ident))
            if row is None:raise FileNotFoundError
            path=self.files/(row.id+row.extension)
            if not path.is_file():raise FileNotFoundError
            return path,row.filename,MIME[row.extension]
