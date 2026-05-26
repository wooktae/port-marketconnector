from flask import Flask, jsonify, request
from get_price_realtime import get_stock_price

app = Flask(__name__)

@app.route("/api/v1/price", methods=["GET"])
def price():
    # 쿼리 파라미터로 종목코드 받기
    stock_code = request.args.get("code")
    if not stock_code:
        return jsonify({"error": "code 파라미터가 필요해"}), 400

    # 실시간 시세 조회
    result = get_stock_price(stock_code)

    if not result:
        return jsonify({"error": "시세를 받아오지 못했어"}), 500

    # 클라이언트 용 JSON 포맷
    data = {
        "code": stock_code,
        "current_price": result.get("current_price"),
        "diff": result.get("diff"),
        "volume": result.get("volume"),
        "open": result.get("open"),
        "high": result.get("high"),
        "low": result.get("low")
    }

    return jsonify(data), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
