"""동행복권 파워볼 당첨결과 페이지에서 복사해온 텍스트를 파싱한다.
한 회차는 9줄 단위: 추첨일 / 회차 / 번호5개(콤마구분) / 파워볼 / 숫자합 / 홀짝 / 대중소 / 숫자합구간 / 파워볼구간
"""


def parse_powerball_block(text: str) -> tuple:
    """붙여넣은 텍스트를 파싱해 회차 리스트로 변환.
    반환: (rounds, errors) — rounds는 save_powerball_rounds()에 바로 넘길 수 있는 dict 리스트.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    rounds = []
    errors = []

    for i in range(0, len(lines), 9):
        chunk = lines[i:i + 9]
        if len(chunk) < 9:
            errors.append(f"마지막 {len(chunk)}줄은 9줄 단위를 채우지 못해 건너뜀: {' / '.join(chunk)}")
            break

        date, round_no, nums_raw, pb_raw, sum_raw, oe_raw, size, sum_band, pb_band = chunk
        block_no = i // 9 + 1
        try:
            nums = [int(n.strip()) for n in nums_raw.split(',')]
            if len(nums) != 5:
                raise ValueError(f"번호가 5개가 아님: {nums_raw}")

            pb = int(pb_raw)

            try:
                total = int(sum_raw)
            except ValueError:
                total = sum(nums)

            oe = '짝' if oe_raw.startswith('짝') else ('홀' if oe_raw.startswith('홀') else oe_raw[:1])

            rounds.append({
                'round': round_no, 'date': date, 'nums': nums, 'pb': pb,
                'sum': total, 'oe': oe, 'size': size, 'sum_band': sum_band, 'pb_band': pb_band,
            })
        except (ValueError, IndexError) as e:
            errors.append(f"{block_no}번째 블록 오류 — {e}")

    return rounds, errors
