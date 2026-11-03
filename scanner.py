# -*- coding: utf-8 -*-
"""
eSCL (AirScan / Mopria Scan) 扫描客户端
直连 HP Color LaserJet Pro MFP 4303，通过 HTTP 逐页取图，实现"扫描一张、处理一张"。
"""
import httpx

SCAN_NS = "http://schemas.hp.com/imaging/escl/2011/05/03"
PWG_NS = "http://www.pwg.org/schemas/2010/12/sm"
A4_W = 2480
A4_H = 3508


class EsclError(RuntimeError):
    pass


class ScannerClient:
    def __init__(self, base_url: str = "http://192.168.31.15:80/eSCL", timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout)

    def scanner_status(self) -> dict:
        r = self._client.get(f"{self.base_url}/ScannerStatus")
        r.raise_for_status()
        return _parse_status(r.text)

    def capabilities(self) -> str:
        r = self._client.get(f"{self.base_url}/ScannerCapabilities")
        r.raise_for_status()
        return r.text

    def create_job(self, source: str = "AdfSimplex", color: str = "Grayscale8",
                   dpi: int = 300, width: int = A4_W, height: int = A4_H) -> str:
        body = _scan_settings_xml(source, color, dpi, width, height)
        r = self._client.post(
            f"{self.base_url}/ScanJobs",
            content=body,
            headers={"Content-Type": "application/xml"},
        )
        if r.status_code not in (200, 201):
            raise EsclError(f"创建作业失败 HTTP {r.status_code} {r.reason_phrase}: {r.text[:300]}")
        location = r.headers.get("Location") or r.headers.get("location")
        if not location:
            raise EsclError("创建作业响应缺少 Location 头")
        return location

    def _job_url(self, job_uri: str) -> str:
        """Location 头可能是完整绝对 URL，也可能是相对路径，统一处理。"""
        if job_uri.startswith("http://") or job_uri.startswith("https://"):
            return job_uri
        return f"{self.base_url}{job_uri}"

    def next_document(self, job_uri: str) -> bytes | None:
        r = self._client.get(
            f"{self._job_url(job_uri)}/NextDocument",
            headers={"Accept": "image/jpeg"},
        )
        if r.status_code == 200:
            return r.content
        if r.status_code == 404:
            return None   # 无更多页
        if r.status_code == 410:
            raise EsclError("作业已失效(410)")
        if r.status_code == 503:
            raise EsclError("扫描仪忙(503)")
        raise EsclError(f"取页失败 HTTP {r.status_code}: {r.text[:200]}")

    def cancel_job(self, job_uri: str) -> None:
        try:
            self._client.delete(self._job_url(job_uri))
        except Exception:
            pass

    def close(self):
        self._client.close()


def _scan_settings_xml(source, color, dpi, width, height) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{SCAN_NS}" xmlns:pwg="{PWG_NS}">
  <pwg:Version>2.9</pwg:Version>
  <pwg:ScanRegions>
    <pwg:ScanRegion>
      <pwg:Height>{height}</pwg:Height>
      <pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
      <pwg:Width>{width}</pwg:Width>
      <pwg:XOffset>0</pwg:XOffset>
      <pwg:YOffset>0</pwg:YOffset>
    </pwg:ScanRegion>
  </pwg:ScanRegions>
  <pwg:InputSource>{source}</pwg:InputSource>
  <scan:ColorMode>{color}</scan:ColorMode>
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
  <scan:XResolution>{dpi}</scan:XResolution>
  <scan:YResolution>{dpi}</scan:YResolution>
</scan:ScanSettings>
"""


def _parse_status(xml: str) -> dict:
    import re
    state = re.search(r"<pwg:State>([^<]+)</pwg:State>", xml)
    adf = re.search(r"<scan:AdfState>([^<]+)</scan:AdfState>", xml)
    jobs = re.findall(r"<pwg:JobState>([^<]+)</pwg:JobState>", xml)
    return {
        "state": state.group(1) if state else None,
        "adf_state": adf.group(1) if adf else None,
        "job_states": jobs,
    }
