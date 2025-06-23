The Python script you provided implements an automated trading bot using the Flask framework for a web interface and interacts with the 5paisa API (and a simulated Stocko.in API for order placement). Below is a detailed breakdown of its trading strategy and how buy/sell operations work:
Overall Architecture
The application has several key components:
Flask Web Server: Provides a web interface for interacting with the bot (starting, stopping, viewing status, orders, and logs).
Global State Management: Uses a dictionary APP_STATE and a threading.Lock to manage shared resources and ensure thread safety.
Logging: Configured to log messages to both the console and an in-memory deque for retrieval via the web interface.
OrderBookManager Class: Manages the bot's trading history (order book), current position (total quantity, average price), realized PnL, and buy/sell counts. It also persists this data to a JSON file.
CSV Data Loading: Loads scrip (script/instrument) master data from scripmaster.csv into a Pandas DataFrame, used for looking up scrip details (lot size, quantity limit, name, etc.).
Trading Logic: The core execute_trading_strategy function, which is run in a separate thread, contains the rules for buying and selling.
API Interactions: Functions (get_ltp, place_order) to fetch Last Traded Price (LTP) and place orders through external APIs (5paisa for LTP, simulated Stocko.in for orders).
Trading Strategy in Detail
The execute_trading_strategy function is the heart of the bot's decision-making process. It evaluates various conditions based on the current market price (LTP), historical LTP, and the bot's current portfolio state to decide whether to buy or sell.
Key Concepts and Metrics Used:
scrip_code: Unique identifier for the trading instrument.
LOT_SIZE: The minimum quantity that can be traded for the scrip. All trade quantities are multiples of this.
INTITIAL_QUANTITY: A base quantity used in calculating buy quantities.
current_ltp: The most recent Last Traded Price of the scrip.
avg_ltp: The average LTP over a recent history (max 300 data points).
high: The highest LTP in the recent history.
low: The lowest LTP in the recent history.
qty: current_total_quantity - The current total quantity of the scrip held by the bot (positive for long position).
avg_price: current_average_price - The average price at which the current qty was acquired.
buy_count: The number of buy orders executed since the position was initiated (or since it was last fully closed).
sell_count: The number of profit-taking sell orders executed. This specifically tracks sell orders that are part of the profit-taking strategy, not stop-loss or risk-reduction sells.
realized_pnl: Profit/Loss from closed trades.
unrealized_pnl: Profit/Loss on the current open position.
net_pnl: realized_pnl + unrealized_pnl.
Strategy Logic Flow:
Fetch LTP: The bot first retrieves the current_ltp for the configured scrip_code using get_ltp. It also updates in_memory_ltp_history and calculates avg_ltp, high, and low from this history. If LTP cannot be retrieved, the cycle is skipped.
Calculate PnL and Portfolio Status: It gets the current portfolio status, including qty, avg_price, buy_count, sell_count, and PnL figures from the OrderBookManager.
MAX LOSS (Stop-Loss) Implementation:
Condition: If the bot currently holds a position (qty > 0) and the unrealized_loss_percentage (calculated as (avg_price - current_ltp) / avg_price) exceeds a max_loss_percentage.
MAX_ALLOWED_LOSS_TIERS: This dictionary defines different maximum allowed loss percentages based on the buy_count:
buy_count 0-9: 0.3% (30 % Loss)
buy_count 10-19: 0.5% (50 % Loss)
buy_count 20-29: 0.5% (50 % Loss)
buy_count 30-39: 0.4% (40 % Loss)
Action: If the condition is met, a SELL order is placed for the entire current quantity (qty). The execute_trading_strategy function then returns, meaning no further trading decisions are made in that cycle after a stop-loss is triggered.
Initial Buy Condition (When qty == 0):
Condition: If the bot holds no position (qty == 0), it looks for an initial entry point. The conditions are:
avg_ltp must be available.
high must be available.
current_ltp > avg_ltp (price is trending up relative to its recent average).
current_ltp < (high * 0.98) (current price is at least 2% below the recent high, possibly to avoid buying at the absolute peak).
Action: If all conditions are met, it calculates quantity_to_buy as (INTITIAL_QUANTITY // LOT_SIZE) * LOT_SIZE to ensure it's a multiple of LOT_SIZE, and places a BUY order.
New Sell Conditions for Risk Reduction (when qty > 0): These conditions are designed to reduce exposure when the bot has accumulated a large position. They are checked before profit target conditions.
Condition 1 (Break-even sell): If buy_count > 20 AND current_ltp >= avg_price * 1.0 (i.e., current price is at or above the average buy price).
Action: Sells 50% of the current quantity (math.floor((qty * 0.5) / LOT_SIZE) * LOT_SIZE). Importantly, increment_sell_count=False is passed to place_order, meaning this sell does not count towards the sell_count used for profit targets. This is a risk-reduction sell.
Condition 2 (Loss reduction sell): If buy_count > 30 AND current_ltp >= avg_price * 0.98 (i.e., current price is at or above 98% of the average buy price, reducing potential further loss).
Action: Sells 50% of the current quantity (math.floor((qty * 0.5) / LOT_SIZE) * LOT_SIZE). Again, increment_sell_count=False is passed, indicating this is a risk-reduction sell.
Existing Profit Target Conditions (when qty > 0):
profit_targets dictionary: {0: 1.03, 1: 1.05, 2: 1.07}. This means:
After the first profit-taking sell (sell_count == 0), target 3% profit (current_ltp >= avg_price * 1.03).
After the second profit-taking sell (sell_count == 1), target 5% profit (current_ltp >= avg_price * 1.05).
After the third profit-taking sell (sell_count == 2), target 7% profit (current_ltp >= avg_price * 1.07).
Condition: If sell_count is one of the keys in profit_targets AND current_ltp reaches or exceeds the corresponding profit target.
Action:
If sell_count < 2: Sells 50% of the current quantity.
If sell_count == 2: Sells 100% of the current quantity (closes the entire position).
place_order is called without increment_sell_count=False, so these sells do increment sell_count.
Buy Conditions for Averaging Down (when qty > 0): These conditions are for adding to an existing losing position.
Max Buy Count Check: If buy_count >= 40, no further buys are allowed for averaging down.
Condition 1 (5% price drop): If current_ltp < avg_price * 0.95 (current price has dropped by 5% from the average buy price).
Action: Calculates quantity_to_buy using calculate_Qty(INTITIAL_QUANTITY, buy_count) and places a BUY order.
Condition 2 (7% price drop for buy_count > 20): If buy_count > 20 AND current_ltp < avg_price * 0.93 (current price has dropped by 7% from the average buy price).
Action: Calculates quantity_to_buy using calculate_Qty(INTITIAL_QUANTITY, buy_count) and places a BUY order. This is a deeper averaging down for larger positions.
Condition 3 (9% price drop for buy_count > 30): If buy_count > 30 AND current_ltp < avg_price * 0.91 (current price has dropped by 9% from the average buy price).
Action: Calculates quantity_to_buy using calculate_Qty(INTITIAL_QUANTITY, buy_count) and places a BUY order. This is for very large and deeply losing positions.
calculate_Qty Function: This function determines the quantity to buy when averaging down, based on INTITIAL_QUANTITY and the current buy_count. It uses a progressive multiplier:
buy_count 0-4: quantity_to_trade * 1
buy_count 5-9: quantity_to_trade * 5
buy_count 10-14: quantity_to_trade * 9
buy_count 15-19: quantity_to_trade * 15
buy_count 20-29: quantity_to_trade * 35
buy_count 30-39: quantity_to_trade * 45
Otherwise (for buy_count 40+ as per the check above, this branch wouldn't be hit for buying if buy_count >= 40): quantity_to_trade * 25 This means the bot buys significantly larger quantities as it averages down further.
How Buy/Sell Operations Work
The place_order function handles the actual execution of trades.
Dummy Mode:
If config['dummy_mode'] is True, the order is not sent to any external API.
Instead, it simulates a Completed order at the current ltp.
The simulated order is added to the OrderBookManager, and the portfolio position and PnL are updated as if a real trade occurred. This is useful for testing the strategy without risking real money.
Real Order Placement:
If dummy_mode is False, it first attempts to get the current_ltp to use as the order price.
Access Token: It reads the access_token from access_token.json. This token is required for authentication with the "Stocko.in" API.
API Endpoint and Payload: It constructs the URL and JSON payload for the order placement API call to https://api.stocko.in/api/v1/orders.
exchange: Derived from config['exchange'] (e.g., "NSE" -> "NFO", "BSE" -> "BFO").
order_type: Always "MARKET".
instrument_token: scrip_code.
quantity: The calculated quantity.
order_side: "BUY" or "SELL".
Other fields like price, trigger_price are set to 0 as it's a market order.
HTTP Request: Sends a POST request to the API with appropriate headers (Content-Type and Authorization).
Response Handling:
If the response status code is 200 and the API response indicates "success", the order is considered placed successfully. The order_id from the API response is captured.
The order details are then added to the OrderBookManager using add_order.
Crucially, order_book_manager.update_position_and_pnl is called to update the bot's internal tracking of its position, average price, and realized PnL.
The increment_sell_count parameter in update_position_and_pnl determines if a sell operation contributes to the sell_count (used for profit targets) or not (used for risk-reduction sells).
If the API call fails or returns an error, an error is logged.
OrderBookManager Role in Buy/Sell
The OrderBookManager is critical for tracking the state of the bot's trading.
add_order(order_details): Records every placed order (simulated or real) in an in-memory order_book (a deque with a max size) and persists it to a JSON file (order_book_<scrip_code>.json). This provides a historical record.
update_position_and_pnl(order_details, current_ltp, increment_sell_count): This method is called after an order is "completed" (or simulated as completed).
For BUY orders:
It updates current_total_quantity by adding the bought quantity.
It recalculates current_average_price to reflect the new average cost of the total quantity held.
It increments buy_count.
For SELL orders:
It calculates the profit/loss for the sold quantity based on the current_average_price and the price at which it was sold. This PnL is added to realized_pnl.
It decreases current_total_quantity.
If increment_sell_count is True (for profit-taking sells), it increments sell_count.
If the current_total_quantity becomes zero, it means the position is fully closed, and current_average_price, buy_count, and sell_count are reset to zero.
The _adjust_buy_count method attempts to keep buy_count aligned with the remaining quantity, which is an interesting detail for tracking averaging down.
Persistence: The _save_persisted_data() method is called after every change to ensure the bot's state is saved to disk, so it can resume from where it left off even if the application restarts.
Summary of Strategy Mechanics
The strategy is a reactive, averaging-down, and profit-taking system with stop-loss features:
Initial Entry: Buys when LTP shows some upward momentum but isn't at its recent peak.
Averaging Down: Aggressively buys more as the price drops further from the average buy price, with increasing multipliers for deeper drops and higher buy_count. This indicates a belief in mean reversion or an attempt to lower the overall average cost.
Stop-Loss: Implements a maximum allowed unrealized loss based on buy_count tiers, triggering a full liquidation to prevent large losses. The defined tiers imply tolerance for higher percentage losses as the buy_count increases, though the 0.9 might be a typo for 0.009.
Risk Reduction Sells: For heavily averaged-down positions (buy_count > 20 or > 30), it tries to sell 50% of the quantity at or near the average price to reduce exposure, without incrementing the profit-taking sell_count.
Profit-Taking: Sells portions (or all) of the position when specific profit targets (3%, 5%, 7%) are met, based on the sell_count.
This strategy appears to be designed for instruments that exhibit mean-reverting behavior, where dips are eventually recovered, allowing for averaging down and subsequent profit-taking. The progressive buy multipliers for averaging down indicate a high-conviction approach to adding to losing positions. The stop-loss is crucial, but its thresholds should be carefully evaluated.
