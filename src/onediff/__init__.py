__version__ = "0.2.0"
__author__ = "OneFlow"
__credits__ = "OneFlow contributors"
import oneflow as flow
flow.mock_torch.enable()
from .pipeline_stable_diffusion_oneflow import OneFlowStableDiffusionPipeline
from .pipeline_alt_diffusion_oneflow import OneFlowAltDiffusionPipeline
from .pipeline_stable_diffusion_inpaint_oneflow import OneFlowStableDiffusionInpaintPipeline