"""
通用 CRUD 基类。

所有业务 CRUD 继承它，获得统一的「查/分页/增/改/删」能力，
避免每个模块重复写 offset/limit/count 逻辑。
"""

from typing import Any, Dict, Generic, List, Optional, Sequence, Tuple, Type, TypeVar

from sqlalchemy import Select, delete, func, select
from sqlalchemy.orm import Session

from app.db.base import Base

ModelType = TypeVar("ModelType", bound=Base)


class CRUDBase(Generic[ModelType]):
    """泛型 CRUD。"""

    def __init__(self, model: Type[ModelType]) -> None:
        self.model = model

    # ---------------- 查询 ----------------
    def get(self, db: Session, obj_id: int) -> Optional[ModelType]:
        """按主键取单条。"""
        return db.get(self.model, obj_id)

    def get_one(self, db: Session, stmt: Select) -> Optional[ModelType]:
        """按自定义条件取单条。"""
        return db.scalars(stmt.limit(1)).first()

    def list_all(self, db: Session, stmt: Optional[Select] = None) -> List[ModelType]:
        """取全部（慎用，仅适合字典类小表）。"""
        stmt = stmt if stmt is not None else select(self.model)
        return list(db.scalars(stmt).all())

    def count(self, db: Session, stmt: Select) -> int:
        """统计满足条件的记录数（兼容 join / distinct）。"""
        subquery = stmt.order_by(None).subquery()
        return int(db.scalar(select(func.count()).select_from(subquery)) or 0)

    def paginate(
        self,
        db: Session,
        stmt: Select,
        page: int = 1,
        page_size: int = 10,
    ) -> Tuple[List[ModelType], int]:
        """
        统一分页：返回 (当前页数据, 总数)。

        :param stmt: 已拼好 where/order_by 的 select 语句
        """
        total = self.count(db, stmt)
        if total == 0:
            return [], 0
        items = db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all()
        return list(items), total

    # ---------------- 写入 ----------------
    def create(self, db: Session, values: Dict[str, Any], commit: bool = True) -> ModelType:
        """新增一条记录。"""
        obj = self.model(**values)
        db.add(obj)
        if commit:
            db.commit()
            db.refresh(obj)
        else:
            db.flush()
        return obj

    def update(self, db: Session, db_obj: ModelType, values: Dict[str, Any], commit: bool = True) -> ModelType:
        """按传入字段更新（None 值由调用方过滤）。"""
        for field, value in values.items():
            setattr(db_obj, field, value)
        db.add(db_obj)
        if commit:
            db.commit()
            db.refresh(db_obj)
        else:
            db.flush()
        return db_obj

    def remove(self, db: Session, db_obj: ModelType, commit: bool = True) -> None:
        """物理删除。"""
        db.delete(db_obj)
        if commit:
            db.commit()

    def remove_by_ids(self, db: Session, ids: Sequence[int]) -> int:
        """按主键批量删除，返回受影响行数。"""
        result = db.execute(delete(self.model).where(self.model.id.in_(list(ids))))
        db.commit()
        return int(result.rowcount or 0)
