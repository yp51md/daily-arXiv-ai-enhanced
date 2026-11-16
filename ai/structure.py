from pydantic import BaseModel, Field

class Structure(BaseModel):
    tldr: str | None = Field(default=None, description="generate a too long; didn't read summary")
    motivation: str | None = Field(default=None, description="describe the motivation in this paper")
    method: str | None = Field(default=None, description="method of this paper")
    result: str | None = Field(default=None, description="result of this paper")
    conclusion: str | None = Field(default=None, description="conclusion of this paper")
