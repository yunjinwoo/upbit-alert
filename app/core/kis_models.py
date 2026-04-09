from dataclasses import dataclass, field, fields, asdict
from typing import List, Optional, Annotated, Any

# 메타데이터 정의 (필수 여부 기록용)
REQUIRED = {"required": True}
OPTIONAL = {"required": False}

@dataclass
class RequestHeader:
    """국내주식 등락률 순위 API 요청 헤더 구조"""
    authorization: str    # 접근토큰
    appkey: str           # 앱키
    appsecret: str        # 앱시크릿키
    tr_id: str            # 거래ID
    custtype: str         # 고객 타입
    content_type: str = field(default="application/json", metadata={"header_name": "content-type"})
    personalseckey: Optional[str] = field(default=None, metadata=OPTIONAL) # 고객식별키
    tr_cont: Optional[str] = field(default=None, metadata=OPTIONAL)        # 연속 거래 여부
    seq_no: Optional[str] = field(default=None, metadata=OPTIONAL)         # 일련번호
    mac_address: Optional[str] = field(default=None, metadata=OPTIONAL)    # 맥주소
    phone_number: Optional[str] = field(default=None, metadata=OPTIONAL)   # 핸드폰번호
    ip_addr: Optional[str] = field(default=None, metadata=OPTIONAL)        # 접속 단말 공인 IP
    gt_uid: Optional[str] = field(default=None, metadata=OPTIONAL)         # Global UID

    def to_dict(self):
        """HTTP 헤더용 딕셔너리로 변환 (하이픈 처리)"""
        header_dict = {
            "content-type": self.content_type,
            "authorization": self.authorization,
            "appkey": self.appkey,
            "appsecret": self.appsecret,
            "tr_id": self.tr_id,
            "custtype": self.custtype
        }
        if self.personalseckey: header_dict["personalseckey"] = self.personalseckey
        if self.tr_cont: header_dict["tr_cont"] = self.tr_cont
        if self.seq_no: header_dict["seq_no"] = self.seq_no
        if self.mac_address: header_dict["mac_address"] = self.mac_address
        if self.phone_number: header_dict["phone_number"] = self.phone_number
        if self.ip_addr: header_dict["ip_addr"] = self.ip_addr
        if self.gt_uid: header_dict["gt_uid"] = self.gt_uid
        return header_dict

@dataclass
class RequestQueryParam:
    """국내주식 등락률 순위 API 요청 파라미터 (QueryParam)"""
    fid_rsfl_rate2: Annotated[str, "등락 비율2"] = field(metadata=REQUIRED)
    fid_cond_mrkt_div_code: Annotated[str, "조건 시장 분류 코드"] = field(metadata=REQUIRED)
    fid_cond_scr_div_code: Annotated[str, "조건 화면 분류 코드"] = field(metadata=REQUIRED)
    fid_input_iscd: Annotated[str, "입력 종목코드"] = field(metadata=REQUIRED)
    fid_rank_sort_cls_code: Annotated[str, "순위 정렬 구분 코드"] = field(metadata=REQUIRED)
    fid_input_cnt_1: Annotated[str, "입력 수1"] = field(metadata=REQUIRED)
    fid_prc_cls_code: Annotated[str, "가격 구분 코드"] = field(metadata=REQUIRED)
    fid_input_price_1: Annotated[str, "입력 가격1"] = field(metadata=REQUIRED)
    fid_input_price_2: Annotated[str, "입력 가격2"] = field(metadata=REQUIRED)
    fid_vol_cnt: Annotated[str, "거래량 수"] = field(metadata=REQUIRED)
    fid_trgt_cls_code: Annotated[str, "대상 구분 코드"] = field(metadata=REQUIRED)
    fid_trgt_exls_cls_code: Annotated[str, "대상 제외 구분 코드"] = field(metadata=REQUIRED)
    fid_div_cls_code: Annotated[str, "분류 구분 코드"] = field(metadata=REQUIRED)
    fid_rsfl_rate1: Annotated[str, "등락 비율1"] = field(metadata=REQUIRED)

@dataclass
class FluctuationRankingItem:
    """국내주식 등락률 순위 개별 종목 정보 (Response Output)"""
    hts_kor_isnm: str        # HTS 한글 종목명
    stck_shrn_iscd: str      # 주식 단축 종목코드
    data_rank: str           # 데이터 순위
    stck_prpr: str           # 주식 현재가
    prdy_vrss_sign: str      # 전일 대비 부호
    prdy_vrss: str           # 전일 대비
    prdy_ctrt: str           # 전일 대비율 (등락률)
    acml_vol: str            # 누적 거래량
    prdy_vol: str            # 전일 거래량
    lstn_stcn: str           # 상장 주식 수
    stck_fcam: str           # 주식 액면가
    cp_cls_code: str         # 자본금 분류 코드
    prdy_vol_rvrt: str = "0" # 전일 거래량 회전율 (또는 비중)

@dataclass
class FluctuationRankingResponse:
    """국내주식 등락률 순위 API 전체 응답 구조"""
    rt_cd: str               # 성공 실패 여부 (0:성공, 0 이외:실패)
    msg_cd: str              # 응답코드
    msg1: str                # 응답메세지
    output: List[FluctuationRankingItem] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict):
        output_data = data.get("output", [])
        parsed_items = []
        for item in output_data:
            code = item.get("stck_shrn_iscd") or item.get("mksc_shrn_iscd") or "000000"
            item_obj = FluctuationRankingItem(
                hts_kor_isnm=item.get("hts_kor_isnm", "N/A"),
                stck_shrn_iscd=code,
                data_rank=item.get("data_rank", "0"),
                stck_prpr=item.get("stck_prpr", "0"),
                prdy_vrss_sign=item.get("prdy_vrss_sign", "3"),
                prdy_vrss=item.get("prdy_vrss", "0"),
                prdy_ctrt=item.get("prdy_ctrt", "0"),
                acml_vol=item.get("acml_vol", "0"),
                prdy_vol=item.get("prdy_vol", "0"),
                lstn_stcn=item.get("lstn_stcn", "0"),
                stck_fcam=item.get("stck_fcam", "0"),
                cp_cls_code=item.get("cp_cls_code", "0"),
                prdy_vol_rvrt=item.get("prdy_vol_rvrt", "0")
            )
            parsed_items.append(item_obj)
        
        return cls(
            rt_cd=data.get("rt_cd", "1"),
            msg_cd=data.get("msg_cd", ""),
            msg1=data.get("msg1", ""),
            output=parsed_items
        )
