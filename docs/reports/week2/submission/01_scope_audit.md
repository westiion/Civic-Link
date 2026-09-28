# 서비스 범위와 Graphability Audit

10개 카테고리의 40개 서비스를 다룬다. 증명 서비스 36개와 생계급여 신청·주거급여 신청·긴급복지 생계지원 요청·기초연금 신청 4개를 포함한다. 복지 분야는 증명 3개와 신청·지원 4개, 총 7개다.

## 서비스 등록부

| ID | 서비스 | 카테고리 | 진입 출처 | 관측 Max Hop | 분류 | 공식 진입 원문 |
| --- | --- | --- | --- | --- | --- | --- |
| SJ-SVC-001 | 주민등록표 등본 | 주민등록 | D01 | 2 | multi-hop | [G004](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13100000015&HighCtgCD=A01010001&Mcode=1020&tp_seq=01) |
| SJ-SVC-002 | 주민등록표 초본 | 주민등록 | D01 | 2 | multi-hop | [G004](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13100000015&HighCtgCD=A01010001&Mcode=1020&tp_seq=01) |
| SJ-SVC-003 | 토지대장 등본 | 토지·건축·부동산 | D02 | 1 | single-hop | [G006](https://m.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13100000026&HighCtgCD=A02001001&Mcode=10207&tp_seq=01) |
| SJ-SVC-004 | 임야대장 등본 | 토지·건축·부동산 | D02 | 1 | single-hop | [G006](https://m.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13100000026&HighCtgCD=A02001001&Mcode=10207&tp_seq=01) |
| SJ-SVC-005 | 토지이용계획확인서 | 토지·건축·부동산 | D03 | 2 | multi-hop | [G043](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=15000000013) |
| SJ-SVC-006 | 건축물대장 등·초본 | 토지·건축·부동산 | D04 | 1 | single-hop | [G045](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=15000000098) |
| SJ-SVC-007 | 개별공시지가확인서 | 토지·건축·부동산 | D05 | 2 | multi-hop | [G010](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=15000000012) |
| SJ-SVC-008 | 부동산 등기사항증명서 | 토지·건축·부동산 | D06 | 2 | multi-hop | [L부동산등기법](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=265377&efYd=20250131&chrClsCd=010202) |
| SJ-SVC-009 | 자동차등록원부 갑부 | 자동차 | D07 | 1 | single-hop | [G047](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=15000000334) |
| SJ-SVC-010 | 자동차등록원부 을부 | 자동차 | D07 | 1 | single-hop | [G047](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=15000000334) |
| SJ-SVC-011 | 가족관계증명서 | 가족관계·제적 | D08 | 2 | multi-hop | [L가족관계의 등록 등에 관한 법률](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=257203&efYd=20241227&chrClsCd=010202) |
| SJ-SVC-012 | 기본증명서 | 가족관계·제적 | D08 | 2 | multi-hop | [L가족관계의 등록 등에 관한 법률](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=257203&efYd=20241227&chrClsCd=010202) |
| SJ-SVC-013 | 혼인관계증명서 | 가족관계·제적 | D08 | 2 | multi-hop | [L가족관계의 등록 등에 관한 법률](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=257203&efYd=20241227&chrClsCd=010202) |
| SJ-SVC-014 | 입양관계증명서 | 가족관계·제적 | D08 | 2 | multi-hop | [L가족관계의 등록 등에 관한 법률](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=257203&efYd=20241227&chrClsCd=010202) |
| SJ-SVC-015 | 제적등본 | 가족관계·제적 | D09 | 3 | multi-hop | [G015](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=12700000044) |
| SJ-SVC-016 | 제적초본 | 가족관계·제적 | D09 | 3 | multi-hop | [G015](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=12700000044) |
| SJ-SVC-017 | 국민기초생활수급자증명서 | 복지 | D10 | 1 | single-hop | [G016](https://m.gov.kr/mw/AA020InfoCappView.do?CappBizCD=14600000280&Mcode=10020) |
| SJ-SVC-018 | 장애인증명서 | 복지 | D11 | 1 | single-hop | [G017](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=14600000273&HighCtgCD=A05001) |
| SJ-SVC-019 | 한부모가족증명서 | 복지 | D12 | 2 | multi-hop | [G018](https://m.gov.kr/mw/AA020InfoCappView.do?CappBizCD=10601000001) |
| SJ-SVC-020 | 지방세 세목별 과세증명서 | 지방세 | D13 | 2 | multi-hop | [G021](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13100000084) |
| SJ-SVC-021 | 지방세 납세증명서 | 지방세 | D14 | 1 | single-hop | [G022](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13100000056) |
| SJ-SVC-022 | 사업자등록증명 | 국세·개인사업 | D15 | 2 | multi-hop | [G023](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=12100000016) |
| SJ-SVC-023 | 휴업사실증명 | 국세·개인사업 | D16 | 2 | multi-hop | [G024](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=12100000017) |
| SJ-SVC-024 | 폐업사실증명 | 국세·개인사업 | D17 | 2 | multi-hop | [G025](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=12100000019) |
| SJ-SVC-025 | 국세 납세증명서 | 국세·개인사업 | D18 | 2 | multi-hop | [G026](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=12100000011) |
| SJ-SVC-026 | 국세 납부내역증명 | 국세·개인사업 | D19 | 2 | multi-hop | [G027](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=12100000018) |
| SJ-SVC-027 | 소득금액증명 | 국세·개인사업 | D20 | 1 | single-hop | [G044](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=12100000021) |
| SJ-SVC-028 | 부가가치세 과세표준증명 | 국세·개인사업 | D21 | 2 | multi-hop | [G030](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=12100000331) |
| SJ-SVC-029 | 부가가치세 면세사업자 수입금액증명 | 국세·개인사업 | D22 | 2 | multi-hop | [G031](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=12100000329&HighCtgCD=) |
| SJ-SVC-030 | 초·중·고 졸업증명 | 교육 | D23 | 1 | single-hop | [G032](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13410000020&HighCtgCD=A04001%3BA04007&tp_seq=01) |
| SJ-SVC-031 | 초·중·고 졸업예정증명 | 교육 | D23 | 1 | single-hop | [G032](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13410000020&HighCtgCD=A04001%3BA04007&tp_seq=01) |
| SJ-SVC-032 | 중·고 성적증명 | 교육 | D24 | 1 | single-hop | [G033](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13410000016&tp_seq=02) |
| SJ-SVC-033 | 초·중·고 학교생활기록부 | 교육 | D25 | 1 | single-hop | [G034](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13410000019) |
| SJ-SVC-034 | 검정고시 합격증명 | 교육 | D26 | 1 | single-hop | [G035](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13404000021&HighCtgCD=A04001&Mcode=10020&srhQuery=2022&tp_seq=02) |
| SJ-SVC-035 | 병적증명서 | 병역 | D27 | 1 | single-hop | [G036](https://m.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13000000016) |
| SJ-SVC-036 | 농지대장 등본 | 농지 | D28 | 1 | single-hop | [G037](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13800000014) |
| SJ-SVC-037 | 생계급여 신청 | 복지 | D29 | 1 | single-hop | [L국민기초생활 보장법](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=276653&efYd=20251001&chrClsCd=010202) |
| SJ-SVC-038 | 주거급여 신청 | 복지 | D30 | 2 | multi-hop | [L주거급여법](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=250087&efYd=20231019&chrClsCd=010202) |
| SJ-SVC-039 | 긴급복지 생계지원 요청 | 복지 | D31 | 1 | single-hop | [L긴급복지지원법](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=270789&efYd=20251023&chrClsCd=010202) |
| SJ-SVC-040 | 기초연금 신청 | 복지 | D32 | 2 | multi-hop | [L기초연금법](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=276657&efYd=20251001&chrClsCd=010202) |

## 카테고리별 감사

| 카테고리 | 서비스 수 | single-hop | multi-hop | 관측 최대 |
| --- | --- | --- | --- | --- |
| 가족관계·제적 | 6 | 0 | 6 | 3 |
| 교육 | 5 | 5 | 0 | 1 |
| 국세·개인사업 | 8 | 1 | 7 | 2 |
| 농지 | 1 | 1 | 0 | 1 |
| 병역 | 1 | 1 | 0 | 1 |
| 복지 | 7 | 4 | 3 | 2 |
| 자동차 | 2 | 2 | 0 | 1 |
| 주민등록 | 2 | 0 | 2 | 2 |
| 지방세 | 2 | 1 | 1 | 2 |
| 토지·건축·부동산 | 6 | 3 | 3 | 2 |

single-hop 18개, multi-hop 22개다. direct-only와 unresolved 서비스는 현재 등록부에 없지만 개별 질문은 0-hop 또는 답변 불가일 수 있다.

## 진입 문서와 참조 경로

진입 문서는 Source List의 D번호로 고정한다. 동일 서비스도 질문에 따라 직접 답할 수 있으므로 서비스 분류를 모든 질문에 일괄 적용하지 않는다.

- direct-only: 진입 문서의 본문·표에서 답하는 0-hop 질문만 확인됨.
- single-hop: 진입 문서가 참조하는 외부 문서 한 단계에서 추가 근거를 얻음.
- multi-hop: 외부 문서가 다시 참조하는 문서까지 필요한 2단계 이상 경로를 확인함.
- unresolved: 필요한 근거의 본문·시행본·적용 조건을 확인하지 못함.

같은 법령의 같은 시행본 안에서 조문을 옮기거나 별표·서식을 여는 것은 문서 hop을 추가하지 않는다. 조문 참조 간선은 별도로 남긴다. 여러 문서를 병렬로 비교하는 multi-source와 연쇄 참조인 multi-hop을 구분한다. 직접 참조가 존재하면 법→영→규칙 순서로 우회해 깊이를 늘리지 않는다. 화면 클릭 횟수는 hop이 아니다.

40개 서비스는 single-hop 18개, multi-hop 22개로 분류하며 기록된 Max Hop은 1–3이다. Max Hop은 지정한 질문의 문서 참조 경로 중 가장 긴 경로의 길이다.

| 서비스 | 진입 | Max Hop / 분류 | 확인된 경로·판정 |
| --- | --- | --- | --- |
| 001–002 | D01 | 2 / multi-hop | 정부24→주민등록 규칙 제18조→기초생활보장법 제2조제2호. 성남시 무인 무료는 조례 제7조제1항제6호. |
| 003–004 | D02 | 1 / single-hop | 정부24가 공간정보법 제75조·규칙 제74조를 모두 직접 참조. 법을 경유한 2-hop은 불필요. |
| 005 | D03 | 2 / multi-hop | 정부24 수수료 조례 참조→성남시 조례 제7조→기초생활보장법 제2조제2호. |
| 006 | D04 | 1 / single-hop | 정부24→건축물대장 규칙 제11조. 건축법 시행령도 직접 참조되어 평면도 조건은 병렬 문서 결합. |
| 007 | D05 | 2 / multi-hop | 정부24 수수료 조례 참조→성남시 조례 제7조→기초생활보장법 제2조제2호. |
| 008 | D06 | 2 / multi-hop | 법 제19조→등기규칙 제28조→인터넷 열람 지침 제10·11조. 최초 열람 후 1시간 내 재열람 조건. |
| 009–010 | D07 | 1 / single-hop | 정부24→자동차등록규칙 제10–12조. 일반 신청과 이해관계인 특례·개인정보 생략 구분. |
| 011–014 | D08 | 2 / multi-hop | 가족관계등록법→규칙 제28조→기초생활보장법 제2조제2호. 수수료 면제 대상 정의. |
| 015–016 | D09 | 3 / multi-hop | 정부24→가족관계등록법→규칙 제28조→기초생활보장법 제2조제2호. 안내부터 시작해 앞 단계가 하나 더 있음. |
| 017 | D10 | 1 / single-hop | 정부24→기초생활보장법·규칙 제40조. 교육급여만의 증명 발급기관 구분. |
| 018 | D11 | 1 / single-hop | 정부24→장애인복지법 시행규칙 제9조. 증명 발급과 장애연금 수급요건을 분리. |
| 019 | D12 | 2 / multi-hop | 정부24→한부모 규칙 제3·3조의3→2026년 고시. 일반 65%, 청소년 한부모 72% 증명 기준. |
| 020 | D13 | 2 / multi-hop | 정부24→성남시 조례 제7조→기초생활보장법 제2조제2호. 신청 유형별 서류는 direct. |
| 021 | D14 | 1 / single-hop | 정부24가 지방세징수법·영·규칙을 직접 참조. 영 제2조 제외액·제7조 유효기간 단축 확인. |
| 022–026 | D15–D19 | 2 / multi-hop | 정부24→국세청 규정 제41조→민원처리법 제28조·영 제32조. 같은 국세청 규정 내 조문 이동은 추가 hop 없음. |
| 027 | D20 | 1 / single-hop | 정부24→소득세법. 국세청 규정은 별도 보완 출처이며 정부24의 직접 참조인 것처럼 연결하지 않음. |
| 028–029 | D21–D22 | 2 / multi-hop | 정부24→국세청 규정 제41조→민원처리법·시행령. 신고·수입금액 증명 구분. |
| 030–033 | D23–D25 | 1 / single-hop | 정부24→국립학교 증명 규칙. 성적증명의 경기도 2006년 조건은 정부24 본문에 있는 direct 정보. |
| 034 | D26 | 1 / single-hop | 정부24→초중등교육법 시행규칙 제39조. 고시 수준·증명 유형별 서식 구분. |
| 035 | D27 | 1 / single-hop | 정부24→병역법 규칙 제8조·병적증명 발급 규정 제5조. 가족 대리·초본 대체·기간 조건 확인. |
| 036 | D28 | 1 / single-hop | 정부24가 농지법·영·규칙 제58조를 모두 직접 참조. 규칙은 2026-09-22 시행본 적용. |
| 037 | D29 | 1 / single-hop | 법 제21조→시행규칙 제34조. 급여 신청서·조건부 첨부자료. |
| 038 | D30 | 2 / multi-hop | 주거급여법 제4조→기초생활보장법 제21조→시행규칙 제34조. 조건부 제적등본. |
| 039 | D31 | 1 / single-hop | 법 제9·13조→시행령 제2·7조. 위기사유와 소득·재산 요건 결합. |
| 040 | D32 | 2 / multi-hop | 기초연금법 제10조→시행규칙 제6조→민법 제777조. 적법한 친족 대리의 범위. |