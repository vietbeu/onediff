#!/bin/bash
# set -x
set -e

MODEL_DIR=
WARMUPS=3
COMPILER=oneflow
OUTPUT_FILE=/dev/stdout

while getopts 'm:w:c:o:h' opt; do
  case "$opt" in
    m)
      MODEL_DIR=$OPTARG
      ;;

    w)
      WARMUPS=$OPTARG
      ;;

    c)
      COMPILER=$OPTARG
      ;;

    o)
      OUTPUT_FILE=$OPTARG
      ;;
   
    ?|h)
      echo "Usage: $(basename $0) [-m model_dir] [-w warmups] [-c compiler] [-o output_file]"
      echo "  -m model_dir: the directory of the models, if not set, use HF models"
      echo "  -w warmups: the number of warmups, default is `${WARMUPS}`"
      echo "  -c compiler: the compiler, default is `${COMPILER}`"
      echo "  -o output_file: the output file, default is `${OUTPUT_FILE}`"
      exit 1
      ;;
  esac
done

SCRIPT_DIR=$(realpath $(dirname $0))

SD15_MODEL_PATH=runwayml/stable-diffusion-v1-5
SD21_MODEL_PATH=stabilityai/stable-diffusion-2-1
SDXL_MODEL_PATH=stabilityai/stable-diffusion-xl-base-1.0

if [ -z "${MODEL_DIR}" ]; then
  echo "model_dir unspecified, use HF models"
  BENCHMARK_QUANT_MODEL=0
else
  echo "model_dir specified, use local models"
  MODEL_DIR=$(realpath ${MODEL_DIR})
  [ -d ${MODEL_DIR}/stable-diffusion-v1-5 ] && SD15_MODEL_PATH=${MODEL_DIR}/stable-diffusion-v1-5
  [ -d ${MODEL_DIR}/stable-diffusion-2-1 ] && SD21_MODEL_PATH=${MODEL_DIR}/stable-diffusion-2-1
  [ -d ${MODEL_DIR}/stable-diffusion-xl-base-1.0 ] && SDXL_MODEL_PATH=${MODEL_DIR}/stable-diffusion-xl-base-1.0

  BENCHMARK_QUANT_MODEL=1

  SDXL_QUANT_MODEL_PATH=${MODEL_DIR}/stable-diffusion-xl-base-1.0-int8
fi

BENCHMARK_RESULT_TEXT="| Model | HxW | it/s | E2E Time (s) | CUDA Mem after (GiB) | Host Mem after (GiB) |\n| --- | --- | --- | --- | --- | --- |\n"


benchmark_sd_model_with_one_resolution() {
  model_name=$1
  model_path=$2
  warmups=$3
  compiler=$4
  height=$5
  width=$6
  echo "Run ${model_path} ${height}x${width}..."
  script_output=$(python3 ${SCRIPT_DIR}/text_to_image.py --model ${model_path} --warmups ${warmups} --compiler ${compiler} --height ${height} --width ${width} | tee /dev/tty)

  # Pattern to match:
  # Inference time: 0.560s
  # Iterations per second: 51.9
  # CUDA Mem after: 12.1GiB
  # Host Mem after: 5.3GiB

  inference_time=$(echo "${script_output}" | grep -oP '(?<=Inference time: )\d+\.\d+')
  iterations_per_second=$(echo "${script_output}" | grep -oP '(?<=Iterations per second: )\d+\.\d+')
  cuda_mem_after=$(echo "${script_output}" | grep -oP '(?<=CUDA Mem after: )\d+\.\d+')
  host_mem_after=$(echo "${script_output}" | grep -oP '(?<=Host Mem after: )\d+\.\d+')

  BENCHMARK_RESULT_TEXT="${BENCHMARK_RESULT_TEXT}| ${model_name} | ${height}x${width} | ${iterations_per_second} | ${inference_time} | ${cuda_mem_after} | ${host_mem_after} |\n"
}

benchmark_sd_model() {
  model_name=$1
  model_path=$2
  resolutions=$3
  warmups=${WARMUPS}
  compiler=${COMPILER}
  for resolution in $(echo ${resolutions} | tr ',' ' '); do
    height=$(echo ${resolution} | cut -d'x' -f1)
    width=$(echo ${resolution} | cut -d'x' -f2)
    benchmark_sd_model_with_one_resolution ${model_name} ${model_path} ${warmups} ${compiler} ${height} ${width}
  done
}

benchmark_sd_model sd15 ${SD15_MODEL_PATH} 512x512,768x768,720x1280,1024x1024
benchmark_sd_model sd21 ${SD21_MODEL_PATH} 512x512,768x768,720x1280,1024x1024
benchmark_sd_model sdxl ${SDXL_MODEL_PATH} 512x512,768x768,720x1280,1024x1024

if [ ${BENCHMARK_QUANT_MODEL} != 0 ]; then 
  if [ x"${COMPILER}" == x"oneflow" ]; then
    benchmark_sd_model sdxl_quant ${SDXL_QUANT_MODEL_PATH} 512x512,768x768,720x1280,1024x1024
  fi
fi

echo -e "${BENCHMARK_RESULT_TEXT}" > ${OUTPUT_FILE}
