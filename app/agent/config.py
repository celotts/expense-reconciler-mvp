from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    model: str = Field(default="qwen2.5-coder:7b")
    base_url: str = Field(default="http://localhost:11434")
    num_ctx: int = Field(default=4096, ge=1024, le=16384)
    max_steps: int = Field(default=4, ge=1, le=10)
    timeout_seconds: float = Field(default=30.0, gt=0)
