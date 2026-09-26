from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UsageEvent


async def record(session: AsyncSession, org_id: int, metric: str, operation: str, quantity: float = 1, *,
                 provider: str | None = None, model: str | None = None, input_tokens: int | None = None,
                 output_tokens: int | None = None, cost_usd: float | None = None, source: str | None = None,
                 job_id: int | None = None, commit: bool = True) -> UsageEvent:
    event = UsageEvent(organization_id=org_id, metric=metric, operation=operation, quantity=quantity,
                       provider=provider, model=model, input_tokens=input_tokens, output_tokens=output_tokens,
                       estimated_cost_usd=cost_usd, source=source, job_id=job_id)
    session.add(event)
    if commit:
        await session.commit()
    return event
