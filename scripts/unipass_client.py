"""UNIPASS(관세청 전자통관시스템) Open API 기본 클라이언트.

사용 예::

    from scripts.unipass_client import UnipassClient

    client = UnipassClient()
    root = client.call(
        service_name="retrieveTrrfCd",
        operation="retrieveTrrfCd",
        params={"hsSgn": "8471300000"},
    )
    for item in root.findall(".//tariff"):
        print(item.findtext("hsSgn"), item.findtext("trrfCd"))
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://unipass.customs.go.kr:38010/ext/rest"
DEFAULT_TIMEOUT = 10.0
DEFAULT_CACHE_TTL = 7 * 24 * 3600  # 7일


class UnipassError(RuntimeError):
    """UNIPASS 호출 실패 또는 업무 오류 응답."""


@dataclass
class UnipassClient:
    """UNIPASS Open API 호출을 캡슐화한 얇은 래퍼.

    인증키는 `api_key` 인자 또는 `UNIPASS_API_KEY` 환경변수에서 읽는다.
    응답은 기본적으로 XML 이며 `xml.etree.ElementTree.Element` 로 반환한다.
    """

    api_key: str | None = None
    base_url: str = DEFAULT_BASE_URL
    timeout: float = DEFAULT_TIMEOUT
    session: requests.Session | None = None
    cache_dir: Path | str | None = None
    cache_ttl: float = DEFAULT_CACHE_TTL
    usage_log_dir: Path | str | None = None
    daily_limits: dict[str, int] | None = None
    usage_warning_threshold: float = 0.8

    def __post_init__(self) -> None:
        if self.api_key is None:
            self.api_key = os.environ.get("UNIPASS_API_KEY")
        has_service_keys = any(
            k.startswith("UNIPASS_API_KEY_") and k != "UNIPASS_API_KEY" for k in os.environ
        )
        if not self.api_key and not has_service_keys:
            raise UnipassError(
                "UNIPASS 인증키가 없습니다. UNIPASS_API_KEY 또는 "
                "UNIPASS_API_KEY_<서비스명> 환경변수를 설정하세요."
            )
        self.session = self.session or requests.Session()
        if self.cache_dir is not None:
            self.cache_dir = Path(self.cache_dir)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        if self.daily_limits and not self.usage_log_dir:
            raise UnipassError("daily_limits 사용 시 usage_log_dir 필수")
        if self.usage_log_dir is not None:
            self.usage_log_dir = Path(self.usage_log_dir)
            self.usage_log_dir.mkdir(parents=True, exist_ok=True)

    def get_api_key(self, service_name: str) -> str:
        """서비스명에 해당하는 API 키를 반환.

        우선순위: ``UNIPASS_API_KEY_<SERVICE_UPPER>`` 환경변수 → 생성자/``UNIPASS_API_KEY``.
        UNIPASS 는 서비스별로 별도 인증키(``crkyCn``)를 발급하므로 다수 서비스 사용 시
        서비스별 env 를 각각 설정한다.
        """
        env_key = os.environ.get(f"UNIPASS_API_KEY_{service_name.upper()}")
        if env_key:
            return env_key
        if self.api_key:
            return self.api_key
        raise UnipassError(
            f"서비스 '{service_name}' 용 API 키가 없습니다. "
            f"UNIPASS_API_KEY_{service_name.upper()} 환경변수를 설정하세요."
        )

    def _cache_path(
        self,
        service_name: str,
        operation: str,
        params: Mapping[str, Any],
    ) -> Path:
        assert self.cache_dir is not None
        clean = {k: v for k, v in (params or {}).items() if k != "crkyCn"}
        key_obj = {"s": service_name, "o": operation, "p": dict(sorted(clean.items()))}
        digest = hashlib.sha256(
            json.dumps(key_obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        safe = f"{service_name}_{operation}".replace("/", "_")
        return self.cache_dir / f"{safe}_{digest}.xml"

    def _read_cache(self, path: Path) -> bytes | None:
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > self.cache_ttl:
            logger.debug("UNIPASS 캐시 만료 (age=%.0fs) %s", age, path)
            return None
        logger.debug("UNIPASS 캐시 히트 %s", path)
        return path.read_bytes()

    def _write_cache(self, path: Path, content: bytes) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(content)
        tmp.replace(path)
        logger.debug("UNIPASS 캐시 저장 %s (%d bytes)", path, len(content))

    def _usage_log_path(self, d: date | None = None) -> Path:
        assert self.usage_log_dir is not None
        target = (d or date.today()).strftime("%Y%m%d")
        return self.usage_log_dir / f"{target}.jsonl"

    def _log_usage(self, service_name: str, cache_hit: bool) -> None:
        if self.usage_log_dir is None:
            return
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "service": service_name,
            "cache_hit": cache_hit,
        }
        with self._usage_log_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_daily_usage(
        self,
        service_name: str | None = None,
        d: date | None = None,
    ) -> dict[str, int]:
        """지정일(기본 오늘) 서비스별 실호출 수. ``cache_hit`` 은 제외."""
        if self.usage_log_dir is None:
            return {}
        path = self._usage_log_path(d)
        counts: dict[str, int] = {}
        if not path.exists():
            return {service_name: 0} if service_name else counts
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("cache_hit"):
                    continue
                svc = entry.get("service", "")
                counts[svc] = counts.get(svc, 0) + 1
        if service_name:
            return {service_name: counts.get(service_name, 0)}
        return counts

    def check_daily_limit(self, service_name: str) -> None:
        """한도 초과 시 ``UnipassError``. 80% 도달 시 warning 로그."""
        if not self.daily_limits:
            return
        limit = self.daily_limits.get(service_name)
        if not limit:
            return
        used = self.get_daily_usage(service_name).get(service_name, 0)
        if used >= limit:
            raise UnipassError(f"서비스 '{service_name}' 일일 호출 한도 초과: {used}/{limit}")
        if used >= int(limit * self.usage_warning_threshold):
            logger.warning(
                "UNIPASS 호출 한도 임박: %s %d/%d (%.0f%%)",
                service_name,
                used,
                limit,
                used / limit * 100,
            )

    def call(
        self,
        service_name: str,
        operation: str,
        params: Mapping[str, Any] | None = None,
        *,
        use_cache: bool = True,
        force_refresh: bool = False,
    ) -> ET.Element:
        """단일 UNIPASS 서비스 호출.

        :param service_name: URL 경로에 포함되는 서비스명(예: ``retrieveTrrfCd``)
        :param operation: URL 경로의 오퍼레이션명. 대부분 ``service_name`` 과 동일.
        :param params: 쿼리 파라미터. 인증키(``crkyCn``)는 자동 주입된다.
        :param use_cache: ``cache_dir`` 가 설정된 경우 캐시 조회/저장을 사용할지.
        :param force_refresh: ``True`` 이면 캐시 히트를 무시하고 API 재호출 후 덮어씀.
        :returns: 루트 ``Element``.
        :raises UnipassError: HTTP 실패 또는 응답에 업무 오류 코드가 포함된 경우.
        """
        cache_enabled = bool(self.cache_dir) and use_cache
        cache_path: Path | None = None
        if cache_enabled:
            cache_path = self._cache_path(service_name, operation, params or {})
            if not force_refresh:
                cached = self._read_cache(cache_path)
                if cached is not None:
                    try:
                        root = ET.fromstring(cached)
                        self._log_usage(service_name, cache_hit=True)
                        return root
                    except ET.ParseError:
                        logger.warning("캐시된 응답 파싱 실패, 재호출: %s", cache_path)

        self.check_daily_limit(service_name)

        url = f"{self.base_url}/{service_name}/{operation}"
        query: dict[str, Any] = {"crkyCn": self.get_api_key(service_name)}
        if params:
            query.update(params)

        logger.debug(
            "UNIPASS 호출 %s params=%s", url, {k: v for k, v in query.items() if k != "crkyCn"}
        )
        response = self.session.get(url, params=query, timeout=self.timeout)
        response.raise_for_status()

        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise UnipassError(f"응답 XML 파싱 실패: {exc}") from exc

        tcnt = (root.findtext(".//tCnt") or "").strip()
        if tcnt.startswith("-"):
            err_msg = (root.findtext(".//errMsgCn") or "").strip()
            ntce = (root.findtext(".//ntceInfo") or "").strip()
            raise UnipassError(err_msg or ntce or f"UNIPASS 시스템 오류 (tCnt={tcnt})")

        if cache_enabled and cache_path is not None:
            self._write_cache(cache_path, response.content)

        self._log_usage(service_name, cache_hit=False)
        return root

    def search_hs_sgn(
        self,
        hs_code: str,
        koen_tp: str = "1",
        service_name: str = "hsSgnQry",
        operation: str = "searchHsSgn",
    ) -> list[dict[str, str | None]]:
        """UNIPASS **HS 부호 조회** (API018, ``hsSgnQry/searchHsSgn``).

        응답은 ``<hsSgnSrchRtnVo>`` 루트에 ``<hsSgnSrchRsltVo>`` 행이 FTA 별로
        여러 건 반환된다(예: 8471300000 → 34행). 행 단위로 세율(``txrt``)과
        세율종류(``txtpSgn``) 가 다르므로 전체 행을 그대로 반환한다.

        UNIPASS API 는 **관세율표(trrf)** 를 단독 서비스로 제공하지 않지만,
        본 응답의 ``tax_rate`` 필드에 FTA 별 세율이 포함된다. 단일 "기본세율" 이
        필요하면 ``fta_code == "A"`` 인 행을 사용하라.

        :param hs_code: 10자리 HS 부호(예: ``"8471300000"``).
        :param koen_tp: 한영구분. ``"1"``=한글(기본), ``"2"``=영문.
            한글 모드에서는 ``name_en`` 이, 영문 모드에서는 ``name_kr`` 이 비어 있다.
        :returns: 행 리스트. 결과 없으면 빈 리스트.
        """
        root = self.call(
            service_name=service_name,
            operation=operation,
            params={"hsSgn": hs_code, "koenTp": koen_tp},
        )
        rows: list[dict[str, str | None]] = []
        for item in root.iterfind(".//hsSgnSrchRsltVo"):
            rows.append(
                {
                    "hs_code": item.findtext("hsSgn"),
                    "name_kr": item.findtext("korePrnm"),
                    "name_en": item.findtext("englPrnm"),
                    "tax_rate": item.findtext("txrt"),
                    "fta_code": item.findtext("txtpSgn"),
                    "qty_unit": item.findtext("qtyUt"),
                    "weight_unit": item.findtext("wghtUt"),
                }
            )
        return rows

    def retrieve_trrt(
        self,
        hs_code: str,
        trrt_tp_cd: str | None = None,
        service_name: str = "trrtQry",
        operation: str = "retrieveTrrt",
    ) -> list[dict[str, str | None]]:
        """UNIPASS **관세율 조회** (API030, ``trrtQry/retrieveTrrt``).

        HS 부호별로 관세율 구분코드, 관세율, 단위당 세액, 기준가격, 적용기간 등을
        0..n 건 반환한다. ``hsSgnQry/searchHsSgn`` 의 ``txrt`` 와 달리
        ``aplyStrtDt/aplyEndDt`` 가 포함되어 적용 시점 기반 쿼리에 적합하다.

        :param hs_code: 10자리 HS 부호(필수).
        :param trrt_tp_cd: 관세율 구분코드(옵션, 예: ``"FEU1"``). 지정 시 단일 구분만.
        :returns: 행 리스트. 결과 없으면 빈 리스트.
        """
        params: dict[str, str] = {"hsSgn": hs_code}
        if trrt_tp_cd:
            params["trrtTpcd"] = trrt_tp_cd
        root = self.call(service_name=service_name, operation=operation, params=params)
        rows: list[dict[str, str | None]] = []
        for item in root.iterfind(".//trrtQryRsltVo"):
            rows.append(
                {
                    "hs_code": item.findtext("hsSgn"),
                    "trrt_tp_cd": item.findtext("trrtTpcd"),
                    "trrt_tp_nm": item.findtext("trrtTpNm"),
                    "tax_rate": item.findtext("trrt"),
                    "per_unit_tax": item.findtext("prutXamt"),
                    "base_price": item.findtext("basePrc"),
                    "apply_start": item.findtext("aplyStrtDt"),
                    "apply_end": item.findtext("aplyEndDt"),
                }
            )
        return rows

    def retrieve_stats_sgn_brkd(
        self,
        stats_sgn_tp: str,
        cd_valt_val_nm: str | None = None,
        cd_valt_val: str | None = None,
        service_name: str = "statsSgnQry",
        operation: str = "retrieveStatsSgnBrkd",
    ) -> list[dict[str, str]]:
        """UNIPASS **통계부호 내역 조회** (API019, ``statsSgnQry/retrieveStatsSgnBrkd``).

        내국세율(A01), 관세감면(A02), 분납(A03), 국가코드(A06) 등 통계부호를 반환.

        **중요**: ``stats_sgn_tp`` 값마다 응답 row 태그와 필드 스키마가 다르다
        (예: A01 은 ``<othStatsSgnQryVo>``, A06 은 ``<statsSgnQryVo2>``).
        따라서 본 메서드는 루트 바로 아래 모든 비메타 요소를 row 로 간주하고
        **XML 태그명을 키로** 하는 dict 를 반환한다. 호출자는 ``stats_sgn_tp`` 별로
        예상 필드를 알고 접근해야 한다.

        주요 타입별 필드:
          - A01 내국세율: ``statsSgn``, ``koreBrkd``, ``itxRt``
          - A06 국가코드: ``cdValtVal``, ``cdValtValNm``, ``englAbrtNm``, ``valtValEnglRmrkCn``

        :param stats_sgn_tp: 통계부호구분 (필수). A01~A13.
        :param cd_valt_val_nm: 코드명. A08/A10/A11 조회 시 코드명 또는 코드값 필수.
        :param cd_valt_val: 코드값.
        :returns: row 리스트(각 row 는 태그명→값 dict). 결과 없으면 빈 리스트.
        """
        params: dict[str, str] = {"statsSgnTp": stats_sgn_tp}
        if cd_valt_val_nm:
            params["cdValtValNm"] = cd_valt_val_nm
        if cd_valt_val:
            params["cdValtVal"] = cd_valt_val
        root = self.call(service_name=service_name, operation=operation, params=params)
        meta_tags = {"ntceInfo", "tCnt", "errMsgCn"}
        rows: list[dict[str, str]] = []
        for item in list(root):
            if item.tag in meta_tags:
                continue
            row = {
                child.tag: (child.text or "").strip() for child in item if child.text is not None
            }
            if row:
                rows.append(row)
        return rows

    def retrieve_carg_cscl_prgs_info(
        self,
        carg_mt_no: str | None = None,
        mbl_no: str | None = None,
        hbl_no: str | None = None,
        bl_yy: str | None = None,
        service_name: str = "cargCsclPrgsInfoQry",
        operation: str = "retrieveCargCsclPrgsInfo",
    ) -> dict[str, Any]:
        """UNIPASS **화물통관 진행정보 조회** (API001).

        수입화물의 일반정보와 진행상태별 이벤트를 반환. 데이터는
        **최근 3년 이내** 만 조회 가능('23.06.17 시점 정책).

        조회결과가 단건이면 상세(``items`` 에 1건 + ``events`` 리스트),
        다건이면 기본정보 목록(``items`` 에 N건, ``events`` 는 비어 있음).
        다건 모드는 ``ntceInfo`` 가 ``[N00]`` 으로 시작함.

        :param carg_mt_no: 화물관리번호 (15~19자리). 주 조회키.
        :param mbl_no: Master BL 번호. 지정 시 ``bl_yy`` 필수.
        :param hbl_no: House BL 번호. 지정 시 ``bl_yy`` 필수.
        :param bl_yy: BL 년도 (입항년도, 4자리).
        :returns: ``{"items": list[dict], "events": list[dict], "notice": str}``.
            ``items`` 는 ``<cargCsclPrgsInfoQryVo>`` (단건 상세 또는 다건 목록),
            ``events`` 는 ``<cargCsclPrgsInfoDtlQryVo>`` (처리이력, 단건일 때만),
            ``notice`` 는 ``ntceInfo`` 원본 메시지.
        """
        if not any((carg_mt_no, mbl_no, hbl_no)):
            raise UnipassError("carg_mt_no / mbl_no / hbl_no 중 하나는 지정해야 합니다.")
        if (mbl_no or hbl_no) and not bl_yy:
            raise UnipassError("mbl_no 또는 hbl_no 지정 시 bl_yy 필수")

        params: dict[str, str] = {}
        if carg_mt_no:
            params["cargMtNo"] = carg_mt_no
        if mbl_no:
            params["mblNo"] = mbl_no
        if hbl_no:
            params["hblNo"] = hbl_no
        if bl_yy:
            params["blYy"] = bl_yy

        root = self.call(service_name=service_name, operation=operation, params=params)

        def _row(item: ET.Element) -> dict[str, str]:
            return {
                child.tag: (child.text or "").strip() for child in item if child.text is not None
            }

        items = [_row(el) for el in root.iterfind(".//cargCsclPrgsInfoQryVo")]
        events = [_row(el) for el in root.iterfind(".//cargCsclPrgsInfoDtlQryVo")]
        notice = (root.findtext(".//ntceInfo") or "").strip()

        return {"items": items, "events": events, "notice": notice}

    def retrieve_trif_fxrt_info(
        self,
        qry_yymmdd: str,
        imex_tp: str,
        service_name: str = "trifFxrtInfoQry",
        operation: str = "retrieveTrifFxrtInfo",
    ) -> list[dict[str, str | None]]:
        """UNIPASS **관세환율 정보 조회** (API012, ``trifFxrtInfoQry/retrieveTrifFxrtInfo``).

        지정일의 수입/수출 관세환율을 통화별로 반환. 관세평가 기준환율.

        :param qry_yymmdd: 조회년월일 (YYYYMMDD, 필수).
        :param imex_tp: 수출입구분 (필수). ``"1"``=수출, ``"2"``=수입.
        :returns: 환율 행 리스트. 각 row: country/currency_name/fx_rate/currency_code/apply_start/imex_type.
        """
        if imex_tp not in ("1", "2"):
            raise UnipassError("imex_tp 는 '1'(수출) 또는 '2'(수입) 중 하나")
        params = {"qryYymmDd": qry_yymmdd, "imexTp": imex_tp}
        root = self.call(service_name=service_name, operation=operation, params=params)
        rows: list[dict[str, str | None]] = []
        for item in root.iterfind(".//trifFxrtInfoQryRsltVo"):
            rows.append(
                {
                    "country": item.findtext("cntySgn"),
                    "currency_name": item.findtext("mtryUtNm") or item.findtext("mtryUtlNm"),
                    "fx_rate": item.findtext("fxrt"),
                    "currency_code": item.findtext("currSgn"),
                    "apply_start": item.findtext("aplyBgnDt"),
                    "imex_type": item.findtext("imexTp"),
                }
            )
        return rows


def _configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


if __name__ == "__main__":
    import argparse

    _configure_logging()
    parser = argparse.ArgumentParser(description="UNIPASS API 호출 도우미")
    parser.add_argument("service", help="서비스명(예: retrieveTrrfCd)")
    parser.add_argument("--operation", help="오퍼레이션명. 생략 시 service 와 동일", default=None)
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="쿼리 파라미터. 여러 번 지정 가능",
    )
    args = parser.parse_args()

    parsed_params: dict[str, str] = {}
    for raw in args.param:
        if "=" not in raw:
            parser.error(f"--param 값은 KEY=VALUE 형식이어야 합니다: {raw}")
        key, value = raw.split("=", 1)
        parsed_params[key] = value

    client = UnipassClient()
    root = client.call(
        service_name=args.service,
        operation=args.operation or args.service,
        params=parsed_params,
    )
    print(ET.tostring(root, encoding="unicode"))
