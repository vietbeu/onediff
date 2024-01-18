MODEL = 'runwayml/stable-diffusion-v1-5'
VARIANT = None
CUSTOM_PIPELINE = None
SCHEDULER = 'EulerDiscreteScheduler'
LORA = None
CONTROLNET = None
STEPS = 30
PROMPT = 'best quality, realistic, unreal engine, 4K, a beautiful girl'
SEED = None
WARMUPS = 3
BATCH = 1
HEIGHT = None
WIDTH = None
EXTRA_CALL_KWARGS = None

import importlib
import inspect
import argparse
import time
import json
import torch
from PIL import (Image, ImageDraw)
import oneflow as flow
from onediff.infer_compiler import oneflow_compile


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', type=str, default=MODEL)
    parser.add_argument('--variant', type=str, default=VARIANT)
    parser.add_argument('--custom-pipeline', type=str, default=CUSTOM_PIPELINE)
    parser.add_argument('--scheduler', type=str, default=SCHEDULER)
    parser.add_argument('--lora', type=str, default=LORA)
    parser.add_argument('--controlnet', type=str, default=None)
    parser.add_argument('--steps', type=int, default=STEPS)
    parser.add_argument('--prompt', type=str, default=PROMPT)
    parser.add_argument('--seed', type=int, default=SEED)
    parser.add_argument('--warmups', type=int, default=WARMUPS)
    parser.add_argument('--batch', type=int, default=BATCH)
    parser.add_argument('--height', type=int, default=HEIGHT)
    parser.add_argument('--width', type=int, default=WIDTH)
    parser.add_argument('--extra-call-kwargs',
                        type=str,
                        default=EXTRA_CALL_KWARGS)
    parser.add_argument('--input-image', type=str, default=None)
    parser.add_argument('--control-image', type=str, default=None)
    parser.add_argument('--output-image', type=str, default=None)
    parser.add_argument(
        '--compiler',
        type=str,
        default='oneflow',
        choices=['none', 'oneflow', 'compile', 'compile-max-autotune'])
    return parser.parse_args()


def load_pipe(pipeline_cls,
              model_name,
              variant=None,
              custom_pipeline=None,
              scheduler=None,
              lora=None,
              controlnet=None):
    extra_kwargs = {}
    if custom_pipeline is not None:
        extra_kwargs['custom_pipeline'] = custom_pipeline
    if variant is not None:
        extra_kwargs['variant'] = variant
    if controlnet is not None:
        from diffusers import ControlNetModel
        controlnet = ControlNetModel.from_pretrained(controlnet,
                                                     torch_dtype=torch.float16)
        extra_kwargs['controlnet'] = controlnet
    pipe = pipeline_cls.from_pretrained(model_name,
                                        torch_dtype=torch.float16,
                                        **extra_kwargs)
    if scheduler is not None:
        scheduler_cls = getattr(importlib.import_module('onediff.schedulers'),
                                scheduler, None)
        if scheduler_cls is None:
            print('No optimized scheduler found, use the plain one.')
            scheduler_cls = getattr(importlib.import_module('diffusers'),
                                    scheduler)
        pipe.scheduler = scheduler_cls.from_config(pipe.scheduler.config)
    if lora is not None:
        pipe.load_lora_weights(lora)
        pipe.fuse_lora()
    pipe.safety_checker = None
    pipe.to(torch.device('cuda'))
    return pipe


def compile_pipe(pipe):
    # Compiling text_encoder could make SD21 output out of range values.
    parts = [
        # 'text_encoder',
        # 'text_encoder_2',
        # 'image_encoder',
        'unet',
        'controlnet',
    ]
    for part in parts:
        if getattr(pipe, part, None) is not None:
            print(f'Compiling {part}')
            setattr(pipe, part, oneflow_compile(getattr(pipe, part)))
    vae_parts = [
        'decoder',
        'encoder',
    ]
    for part in vae_parts:
        if getattr(pipe.vae, part, None) is not None:
            print(f'Compiling vae.{part}')
            setattr(pipe.vae, part, oneflow_compile(getattr(pipe.vae, part)))
    return pipe


class IterationProfiler:

    def __init__(self):
        self.begin = None
        self.end = None
        self.num_iterations = 0

    def get_iter_per_sec(self):
        if self.begin is None or self.end is None:
            return None
        self.end.synchronize()
        dur = self.begin.elapsed_time(self.end)
        return self.num_iterations / dur * 1000.0

    def callback_on_step_end(self, pipe, i, t, callback_kwargs):
        if self.begin is None:
            event = torch.cuda.Event(enable_timing=True)
            event.record()
            self.begin = event
        else:
            event = torch.cuda.Event(enable_timing=True)
            event.record()
            self.end = event
            self.num_iterations += 1
        return callback_kwargs


def main():
    args = parse_args()
    if args.input_image is None:
        from diffusers import AutoPipelineForText2Image as pipeline_cls
    else:
        from diffusers import AutoPipelineForImage2Image as pipeline_cls

    pipe = load_pipe(
        pipeline_cls,
        args.model,
        variant=args.variant,
        custom_pipeline=args.custom_pipeline,
        scheduler=args.scheduler,
        lora=args.lora,
        controlnet=args.controlnet,
    )

    height = args.height
    width = args.width
    height = args.height or pipe.unet.config.sample_size * pipe.vae_scale_factor
    width = args.width or pipe.unet.config.sample_size * pipe.vae_scale_factor

    if args.compiler == 'none':
        pass
    elif args.compiler == 'oneflow':
        pipe = compile_pipe(pipe)
    elif args.compiler in ('compile', 'compile-max-autotune'):
        mode = 'max-autotune' if args.compiler == 'compile-max-autotune' else None
        pipe.unet = torch.compile(pipe.unet, mode=mode)
        if hasattr(pipe, 'controlnet'):
            pipe.controlnet = torch.compile(pipe.controlnet, mode=mode)
        pipe.vae = torch.compile(pipe.vae, mode=mode)
    else:
        raise ValueError(f'Unknown compiler: {args.compiler}')

    if args.input_image is None:
        input_image = None
    else:
        input_image = Image.open(args.input_image).convert('RGB')
        input_image = input_image.resize((width, height),
                                         Image.LANCZOS)

    if args.control_image is None:
        if args.controlnet is None:
            control_image = None
        else:
            control_image = Image.new('RGB', (width, height))
            draw = ImageDraw.Draw(control_image)
            draw.ellipse((args.width // 4, height // 4,
                          args.width // 4 * 3, height // 4 * 3),
                         fill=(255, 255, 255))
            del draw
    else:
        control_image = Image.open(args.control_image).convert('RGB')
        control_image = control_image.resize((width, height),
                                             Image.LANCZOS)

    def get_kwarg_inputs():
        kwarg_inputs = dict(
            prompt=args.prompt,
            height=height,
            width=width,
            num_inference_steps=args.steps,
            num_images_per_prompt=args.batch,
            generator=None if args.seed is None else torch.Generator(
                device='cuda').manual_seed(args.seed),
            **(dict() if args.extra_call_kwargs is None else json.loads(
                args.extra_call_kwargs)),
        )
        if input_image is not None:
            kwarg_inputs['image'] = input_image
        if control_image is not None:
            if input_image is None:
                kwarg_inputs['image'] = control_image
            else:
                kwarg_inputs['control_image'] = control_image
        return kwarg_inputs

    # NOTE: Warm it up.
    # The initial calls will trigger compilation and might be very slow.
    # After that, it should be very fast.
    if args.warmups > 0:
        print('Begin warmup')
        for _ in range(args.warmups):
            pipe(**get_kwarg_inputs())
        print('End warmup')

    # Let's see it!
    # Note: Progress bar might work incorrectly due to the async nature of CUDA.
    kwarg_inputs = get_kwarg_inputs()
    iter_profiler = None
    if 'callback_on_step_end' in inspect.signature(pipe).parameters:
        iter_profiler = IterationProfiler()
        kwarg_inputs[
            'callback_on_step_end'] = iter_profiler.callback_on_step_end
    begin = time.time()
    output_images = pipe(**kwarg_inputs).images
    end = time.time()

    print('=======================================')
    print(f'Inference time: {end - begin:.3f}s')
    iter_per_sec = iter_profiler.get_iter_per_sec()
    if iter_per_sec is not None:
        print(f'Iterations per second: {iter_per_sec:.3f}')
    cuda_mem_after_used = flow._oneflow_internal.GetCUDAMemoryUsed()
    host_mem_after_used = flow._oneflow_internal.GetCPUMemoryUsed()
    print(f'CUDA Mem after: {cuda_mem_after_used / 1024:.3f}GiB')
    print(f'Host Mem after: {host_mem_after_used / 1024:.3f}GiB')
    print('=======================================')

    if args.output_image is not None:
        output_images[0].save(args.output_image)
    else:
        print("Please set `--output-image` to save the output-video")


if __name__ == '__main__':
    main()
