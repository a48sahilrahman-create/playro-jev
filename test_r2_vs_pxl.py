import sys
sys.path.insert(0, r'C:\Users\sahil\.claude\skills\play\tools')
import httpx
from stream_verifier import StreamVerifier
import asyncio

async def test():
    v = StreamVerifier()
    # Test R2 presigned URL from Michael
    r2_url = "https://556138dca7367763ed46eecaa4284eca.r2.cloudflarestorage.com/hub2/Still.A.Michael.J.Fox.Movie.2023.1080p.WEB-DL.English.MSubs.Vegamovies.to.mkv?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=26b6cf8a0399b5880643f585c8c3dbe5%2F20260823%2Fauto%2Fs3%2Faws4_request&X-Amz-Date=20260823T083435Z&X-Amz-Expires=10800&X-Amz-SignedHeaders=host&response-content-disposition=Still.A.Michael.J.Fox.Movie.2023.1080p.WEB-DL.English.MSubs.Vegamovies.to.mkv&X-Amz-Signature=4fdacf497adc9a65cbef851a701ca524ce0f889c7278bbd95213e283cf48e2be"
    res = await v.verify(r2_url)
    print("R2 verification result:", res)

    # Test Pixeldrain URL from Michael
    pxl_url = "https://pixeldrain.dev/api/file/wdJKuWbq"
    res_pxl = await v.verify(pxl_url)
    print("Pixeldrain verification result:", res_pxl)

asyncio.run(test())
