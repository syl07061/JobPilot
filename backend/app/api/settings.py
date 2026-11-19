from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException

from app.core.config import PROJECT_ROOT, get_settings
from app.services.model_settings import ModelSettingsStore, ModelSettingsUpdate

router=APIRouter(prefix='/settings',tags=['settings'])


@lru_cache
def get_model_store():
    return ModelSettingsStore(PROJECT_ROOT/'data/settings/model.json',get_settings())


@router.get('/model')
def read_model(store:ModelSettingsStore=Depends(get_model_store)):
    try:return store.get()
    except RuntimeError as exc:raise HTTPException(503,str(exc)) from None


@router.put('/model')
def save_model(body:ModelSettingsUpdate,store:ModelSettingsStore=Depends(get_model_store)):
    try:return store.update(body)
    except RuntimeError as exc:raise HTTPException(503,str(exc)) from None
