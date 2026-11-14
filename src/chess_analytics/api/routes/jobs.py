"""Job status: how clients follow work they requested (e.g. a sync)."""

from fastapi import APIRouter, HTTPException, status

from chess_analytics.api.dependencies import SessionDep
from chess_analytics.api.schemas import JobResponse
from chess_analytics.storage.repositories.jobs import JobRepository

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobResponse, summary="Status of a background job")
def get_job(job_id: int, session: SessionDep) -> JobResponse:
    job = JobRepository(session).get(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"job {job_id} not found")
    return JobResponse.from_job(job)
