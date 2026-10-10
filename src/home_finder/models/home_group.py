"""A saved home's display destination is separate from its personal-state origin."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from home_finder.models.home_identity import is_home_id


class HomeRoute(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    home_id: str
    target_home_id: str | None
    state: Literal["active", "retired", "review_required"] = "active"

    @model_validator(mode="after")
    def validate_route(self) -> "HomeRoute":
        if not is_home_id(self.home_id):
            raise ValueError("Routing requires a stable home identity")
        if self.state == "active":
            if self.target_home_id is None or not is_home_id(self.target_home_id):
                raise ValueError("An active route requires a stable destination")
        elif self.target_home_id is not None:
            raise ValueError("An inactive route cannot choose a destination")
        return self
