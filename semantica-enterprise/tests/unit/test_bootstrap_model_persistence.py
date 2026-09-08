from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from packages.platform.bootstrap import bootstrap
from packages.platform.models import Base, ExtractionPolicy, ModelConfig


def test_bootstrap_does_not_recreate_a_disabled_vision_model() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        bootstrap(db)
        vision = db.scalar(select(ModelConfig).where(ModelConfig.model_kind == "vision"))
        assert vision is not None
        vision.enabled = False
        vision.is_default = False
        db.commit()

        bootstrap(db)

        models = list(db.scalars(select(ModelConfig).where(ModelConfig.model_kind == "vision")))
        assert len(models) == 1
        assert models[0].id == vision.id
        assert models[0].enabled is False
        assert models[0].is_default is False


def test_bootstrap_extraction_policy_follows_model_routing() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        bootstrap(db)
        policy = db.scalar(select(ExtractionPolicy).where(ExtractionPolicy.is_default.is_(True)))

        assert policy is not None
        assert policy.model_config_id is None
