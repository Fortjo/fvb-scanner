import yfinance as yf
h = yf.Ticker("AVGO").history(period="10d")
print(h[["Close"]].tail(10))
