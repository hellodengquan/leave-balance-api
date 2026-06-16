from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import schemas
from app.services.permission_service import PermissionService

router = APIRouter(prefix="/system", tags=["系统与权限管理"])


def _require_admin(
    db: Session = Depends(get_db),
    x_username: Optional[str] = Header(None)
) -> schemas.AuthContext:
    if not x_username:
        raise HTTPException(status_code=401, detail="需要x_username请求头")
    perm = PermissionService(db)
    try:
        auth = perm.get_auth_context(x_username)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    if not perm.has_role(auth, perm.ROLE_ADMIN):
        raise HTTPException(status_code=403, detail="仅管理员可访问")
    return auth


@router.post("/users", response_model=schemas.SysUser, summary="创建系统用户(含角色)")
def create_user(
    data: schemas.SysUserCreate,
    db: Session = Depends(get_db),
    _: schemas.AuthContext = Depends(_require_admin)
):
    perm = PermissionService(db)
    try:
        return perm.create_user(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/users/me", response_model=schemas.AuthContext, summary="获取当前用户权限上下文")
def get_me(
    db: Session = Depends(get_db),
    x_username: Optional[str] = Header(None)
):
    if not x_username:
        raise HTTPException(status_code=400, detail="缺少x_username请求头")
    perm = PermissionService(db)
    try:
        return perm.get_auth_context(x_username)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/users", response_model=List[schemas.SysUser], summary="用户列表")
def list_users(
    db: Session = Depends(get_db),
    _: schemas.AuthContext = Depends(_require_admin)
):
    from app.models.models import SysUser
    return db.query(SysUser).filter(SysUser.is_active == True).all()


@router.get("/roles", summary="角色定义与层级")
def get_role_definitions():
    return {
        "employee": {"level": 10, "desc": "普通员工 - 仅查看和申请本人"},
        "manager": {"level": 20, "desc": "部门经理 - 查看部门员工+审批部门申请"},
        "finance": {"level": 30, "desc": "财务 - 查看所有+导出"},
        "hr": {"level": 40, "desc": "HR - 管理所有员工/假期/配置/过期清零审批"},
        "admin": {"level": 999, "desc": "系统管理员 - 全部权限"},
    }
