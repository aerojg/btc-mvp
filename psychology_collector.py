"""
심리 지표 데이터 수집 모듈

- Fear & Greed Index (공포-탐욕지수)
- Google Trends (구글 검색 추이)
- Reddit mentions (레딧 언급)
"""

import requests
import json
from datetime import datetime

# =====================================================
# 1. Fear & Greed Index (공포-탐욕지수)
# =====================================================

def fetch_fear_greed_index():
    """
    Alternative.me Fear & Greed Index 수집
    
    반환값:
    {
        'value': 75,  # 0-100 (높을수록 탐욕, 낮을수록 공포)
        'value_classification': 'Greed',
        'timestamp': '2026-07-02T12:00:00Z'
    }
    
    실패 시 None 반환
    """
    try:
        url = "https://api.alternative.me/fng/?limit=1&format=json"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        if data['data'] and len(data['data']) > 0:
            fng = data['data'][0]
            return {
                'value': int(fng['value']),
                'classification': fng['value_classification'],
                'timestamp': fng['timestamp']
            }
        else:
            print("⚠️  Fear & Greed Index: 데이터 없음")
            return None
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Fear & Greed Index 수집 실패: {e}")
        return None
    except (KeyError, ValueError) as e:
        print(f"❌ Fear & Greed Index 파싱 실패: {e}")
        return None


# =====================================================
# 테스트: 실행 시 수집 함수 테스트
# =====================================================

if __name__ == "__main__":
    print("=" * 50)
    print("심리 지표 데이터 수집 테스트")
    print("=" * 50)
    
    print("\n📊 Fear & Greed Index 수집 중...")
    fng_result = fetch_fear_greed_index()
    if fng_result:
        print(f"✓ FNG 값: {fng_result['value']} ({fng_result['classification']})")
        print(f"  수집 시간: {fng_result['timestamp']}")
    else:
        print("✗ FNG 수집 실패")
    
    print("\n✅ 테스트 완료!")