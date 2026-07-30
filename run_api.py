"""Entry point: start the FastAPI server."""

import uvicorn

from config.settings import settings

if __name__ == "__main__":
    print("Starting FO Intelligence API...")
    print(f"  Data dir:   {settings.resolved_data_dir}")
    print(f"  Chroma dir: {settings.resolved_chroma_dir}")
    print(f"  SEC enrichment:  {'ON' if settings.enable_sec_enrichment else 'OFF'}")
    print(f"  Web enrichment:  {'ON' if settings.enable_web_enrichment else 'OFF'}")
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=False)
