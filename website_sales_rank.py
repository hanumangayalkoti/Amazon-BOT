# coding: utf-8

from __future__ import annotations
import pprint
import json

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr
from typing import Any, ClassVar, Dict, List, Optional, Union
from typing import Optional, Set
from typing_extensions import Self

class WebsiteSalesRank(BaseModel):
    """
    Container for website sales rank information.
    """
    context_free_name: Optional[StrictStr] = Field(default=None, alias="contextFreeName")
    display_name: Optional[StrictStr] = Field(default=None, alias="displayName")
    sales_rank: Optional[Union[StrictFloat, StrictInt]] = Field(default=None, alias="salesRank")
    __properties: ClassVar[List[str]] = ["contextFreeName", "displayName", "salesRank"]

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        protected_namespaces=(),
    )

    def to_str(self) -> str:
        return pprint.pformat(self.model_dump(by_alias=True))

    def to_json(self) -> str:
        return self.model_dump_json(by_alias=True, exclude_unset=True)

    @classmethod
    def from_json(cls, json_str: str) -> Optional[Self]:
        return cls.from_dict(json.loads(json_str))

    def to_dict(self) -> Dict[str, Any]:
        excluded_fields: Set[str] = set([])
        _dict = self.model_dump(
            by_alias=True,
            exclude=excluded_fields,
            exclude_none=True,
        )
        return _dict

    @classmethod
    def from_dict(cls, obj: Optional[Dict[str, Any]]) -> Optional[Self]:
        if obj is None:
            return None
        if not isinstance(obj, dict):
            return cls.model_validate(obj)
        _obj = cls.model_validate({
            "contextFreeName": obj.get("contextFreeName"),
            "displayName": obj.get("displayName"),
            "salesRank": obj.get("salesRank"),
        })
        return _obj
