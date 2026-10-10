import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import not_found
from ..models import Template
from ..schemas import TemplateOut
from ..services import lipsync
from ..services import templates_v3 as tv3

router = APIRouter(tags=["templates"])

CATEGORIES = ["trending", "new", "funny", "cinematic", "dance", "fashion", "travel", "fantasy"]


@router.get("/templates", response_model=list[TemplateOut])
def list_templates(category: str | None = None, locale: str | None = None,
                   limit: int = Query(default=50, le=100), offset: int = 0, db: Session = Depends(get_db)):
    q = select(Template).where(Template.is_active.is_(True), Template.deleted_at.is_(None),
                               Template.current_version_id.isnot(None), Template.visibility == "public",
                               Template.moderation_status == "approved")
    if category == "new":
        q = q.order_by(Template.created_at.desc())
    else:
        if category:
            q = q.where(Template.category == category)
        q = q.order_by(Template.sort_order, Template.created_at.desc())
    if locale:
        # Untagged templates are global; tagged ones only show in their locales.
        q = q.where(or_(Template.locale_tags == [], Template.locale_tags.contains([locale.split("-")[0]])))
    return db.execute(q.limit(limit).offset(offset)).scalars().all()


@router.get("/templates/categories")
def categories():
    return {"categories": CATEGORIES}


@router.get("/templates/{template_id}", response_model=TemplateOut)
def get_template(template_id: uuid.UUID, db: Session = Depends(get_db)):
    t = db.get(Template, template_id)
    if t is None or not tv3.is_usable(t):
        raise not_found("template")
    out = TemplateOut.model_validate(t)
    v = tv3.current_version(db, t)
    slots = tv3.slots_of(db, v)
    if tv3.is_remix(v, slots):
        out.mode = "remix"
        out.person_slots = tv3.slot_out(db, v)
        out.est_credits = tv3.quote(db, t, v, 1, "720x1280", False)["credits"]
        out.lip_sync_available = lipsync.config(db)[0] and bool((v.config or {}).get("speaker_mapping"))
    else:
        out.est_credits = t.credit_cost
    return out
