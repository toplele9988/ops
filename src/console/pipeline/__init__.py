"""Read-only pipeline health; never starts collectors or opens channel clients."""
from .service import PipelineHealth

__all__ = ['PipelineHealth']
