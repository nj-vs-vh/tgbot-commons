from dataclasses import asdict
import json

from typing import Type, TypeVar


_StorableType = TypeVar("_StorableType")


class Storable:
    def to_store(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_store(cls: Type[_StorableType], dump: str) -> _StorableType:
        return cls(**json.loads(dump))
