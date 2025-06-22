# Trading Bot Project

This project implements an automated trading bot using the 5Paisa API and Flask, with a strategy for buying and selling options based on LTP metrics.

## Trading Strategy

The strategy executes trades based on:
- Initial buy when LTP is above average but below 98% of the high.
- Selling at profit targets (3%, 5%, 7%) based on sell count.
- Averaging down if LTP drops 5% below average price.
- This Project including API Structures.
- If you Want to Start Trading On NSE & BSE Option Scrip Call @ 9727429104 Dhaval Patel.
- This Stretagy Only Profit No Loss (Any Market Condition Like Bullish, Bearish or Consolidation)
- Momentum Market More Proftit. Less Volatility Small Profit but Not Loss.

Below is a screenshot of the strategy in action:

![Trading Strategy Screenshot](screenshots/strategy_screenshot.png)

## Installation

```bash
pip install -r requirements.txt
python app.py

git add README.md screenshots/strategy_screenshot.png
git commit -m "Add README with strategy screenshot"
git push origin main
