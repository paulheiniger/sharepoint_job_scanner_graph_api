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
export SAM2_API_KEY="<separate-random-service-secret>"

uvicorn services.roof_sam2.server:app --host 127.0.0.1 --port 8765
```

Then run the Streamlit app with:

```bash
export ROOF_MEASURE_SEGMENTER="sam2_remote"
export SAM2_SEGMENTATION_URL="http://127.0.0.1:8765/segment"
export SAM2_API_KEY="<same-service-secret>"
```

Use the tiny checkpoint first on a laptop. Move the same service to a Mac Studio
later and point `SAM2_SEGMENTATION_URL` at that machine.

The Business Assistant uses the same client but calls SAM2 only after reviewed
footprint IDs have been selected. It ranks every returned mask and requires an
estimator-confirmed candidate ID before calculation. It never invokes the
manual rectangle fallback or an OpenAI API.

Start the checked-in service wrapper with:

```bash
mkdir -p output/roof_sam2
chmod +x scripts/start_roof_sam2.sh
scripts/start_roof_sam2.sh
```

Put optional service-only environment values in the ignored `.env.sam2` file.
Alternatively, set `SAM2_API_KEY_FILE` to a permission-restricted file that
contains only the key value. Do not commit either form of the API key. A launch
agent cannot execute this checkout from the
macOS privacy-protected `Downloads` directory. Move the long-running runtime to
an unprotected service directory before installing it under launchd.

Binding to `127.0.0.1` is appropriate for local API or Streamlit callers. An
Azure-hosted Business API cannot reach that address; use a private authenticated
tunnel or another private network path and set the Business API's
`SAM2_SEGMENTATION_URL` to that reachable HTTPS `/segment` URL.
