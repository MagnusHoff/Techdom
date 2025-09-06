from pydantic import BaseModel


class Suggestion(BaseModel):
    suggested_rent: int
    low_ci: int
    high_ci: int
    n_comps: int
