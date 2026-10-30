"""Base generic asynchronous repository for SQLAlchemy models."""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.base import Base

ModelType = TypeVar("ModelType", bound=Base)
CreateSchemaType = TypeVar("CreateSchemaType", bound=BaseModel)
UpdateSchemaType = TypeVar("UpdateSchemaType", bound=BaseModel)


class BaseRepository(Generic[ModelType, CreateSchemaType, UpdateSchemaType]):
    """Generic async repository providing standard CRUD operations."""

    def __init__(self, model: type[ModelType], session: AsyncSession) -> None:
        self.model = model
        self.session = session

    async def get_by_id(self, entity_id: Any) -> ModelType | None:
        """Fetch a single record by primary key, using identity map cache if loaded."""
        return await self.session.get(self.model, entity_id)

    async def list_all(self, skip: int = 0, limit: int = 100) -> list[ModelType]:
        """Fetch paginated records."""
        result = await self.session.execute(select(self.model).offset(skip).limit(limit))
        return list(result.scalars().all())

    async def create(self, schema: CreateSchemaType | dict[str, Any]) -> ModelType:
        """Create and persist a new record."""
        data = schema.model_dump() if isinstance(schema, BaseModel) else schema
        db_obj = self.model(**data)
        self.session.add(db_obj)
        await self.session.flush()
        await self.session.refresh(db_obj)
        return db_obj

    async def update(
        self, entity_id: Any, schema: UpdateSchemaType | dict[str, Any]
    ) -> ModelType | None:
        """Update fields of an existing record."""
        db_obj = await self.get_by_id(entity_id)
        if not db_obj:
            return None

        update_data = (
            schema.model_dump(exclude_unset=True) if isinstance(schema, BaseModel) else schema
        )

        for field, value in update_data.items():
            if hasattr(db_obj, field):
                setattr(db_obj, field, value)

        self.session.add(db_obj)
        await self.session.flush()
        await self.session.refresh(db_obj)
        return db_obj

    async def delete(self, entity_id: Any) -> bool:
        """Delete a record by primary key."""
        db_obj = await self.get_by_id(entity_id)
        if not db_obj:
            return False

        await self.session.delete(db_obj)
        await self.session.flush()
        return True
