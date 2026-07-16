# Roof SAM2 Segmentation Service

Optional local service for the `AI Roof Measure` page. It keeps SAM2, torch, and
model checkpoints out of the Streamlit app process.

## Run Locally

```bash
cd /Users/paulheiniger/Downloads/sharepoint_job_scanner_graph_api

python3 -m venv .venv-sam2
source .venv-sam2/bin/activate
pip install -r services/roof_sam2/requirements.txt
pip install -e ./sam2

export SAM2_REPO_PATH="$PWD/sam2"
export SAM2_CHECKPOINT="$PWD/sam2/checkpoints/sam2.1_hiera_tiny.pt"
export SAM2_MODEL_CONFIG="configs/sam2.1/sam2.1_hiera_t.yaml"
export SAM2_DEVICE="auto"

uvicorn services.roof_sam2.server:app --host 127.0.0.1 --port 8765
```

Then run the Streamlit app with:

```bash
export ROOF_MEASURE_SEGMENTER="sam2_remote"
export SAM2_SEGMENTATION_URL="http://127.0.0.1:8765/segment"
```

Use the tiny checkpoint first on a laptop. Move the same service to a Mac Studio
later and point `SAM2_SEGMENTATION_URL` at that machine.
