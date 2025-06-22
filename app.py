import requests
import json
import logging
from datetime import datetime
from time import time, sleep
import os
import math
from flask import Flask, jsonify, request, render_template_string
import threading
from collections import deque
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pandas as pd

YOUR_5PAISA_API_KEY = "Q4O7AsAK0iUABwjsvYfmfNU1cMiMWXai"

# --- In-memory handler for capturing logs ---
class DequeHandler(logging.Handler):
    def __init__(self, deque_instance):
        super().__init__()
        self.deque = deque_instance

    def emit(self, record):
        self.deque.append(self.format(record))

# --- Global State ---
APP_STATE = {
    "bot_thread": None,
    "stop_event": threading.Event(),
    "order_book_manager": None,
    "config": {},
    "logs": deque(maxlen=200),
    "lock": threading.Lock()
}

# --- Logging Configuration ---
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
deque_handler = DequeHandler(APP_STATE["logs"])
deque_handler.setFormatter(log_formatter)
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
logger = logging.getLogger()
logger.setLevel(logging.INFO)
if logger.hasHandlers():
    logger.handlers.clear()
logger.addHandler(deque_handler)
logger.addHandler(console_handler)

# --- In-memory Data Stores ---
in_memory_ltp_history = {}
last_buy_price_for_scrip = {}
df = None

# --- Load CSV Data ---
def load_csv_data():
    """Load CSV data for script details"""
    global df
    try:
        df = pd.read_csv('scripmaster.csv')
        logging.info(f"CSV loaded successfully. Columns: {df.columns.tolist()}, Data shape: {df.shape}")
    except FileNotFoundError:
        logging.info("CSV file not found. Creating sample data...")
        sample_data = {
            'Exch': ['N', 'N', 'N', 'N', 'N', 'N', 'N', 'N', 'B', 'B'],
            'ExchType': ['D', 'D', 'D', 'D', 'D', 'D', 'D', 'D', 'D', 'D'],
            'Name': ['NIFTY 26 JUN 2025 CE 18250.00', 'NIFTY 26 JUN 2025 CE 18290.00', 
                    'NIFTY 26 JUN 2025 CE 18300.00', 'NIFTY 26 JUN 2025 PE 18300.00',
                    'NIFTY 26 JUN 2025 CE 30000.00', 'NIFTY 26 JUN 2025 PE 30000.00',
                    'NIFTY 26 JUN 2025 CE 7000.00', 'NIFTY 26 JUN 2025 PE 7000.00',
                    'BANKNIFTY 26 JUN 2025 CE 17900.00', 'BANKNIFTY 26 JUN 2025 PE 17900.00'],
            'Expiry': ['6/26/2025', '6/26/2025', '6/26/2025', '6/26/2025', '6/26/2025', 
                      '6/26/2025', '6/26/2025', '6/26/2025', '6/26/2025', '6/26/2025'],
            'ScripType': ['CE', 'CE', 'CE', 'PE', 'CE', 'PE', 'CE', 'PE', 'CE', 'PE'],
            'StrikeRate': [18250, 18290, 18300, 18300, 30000, 30000, 7000, 7000, 17900, 17900],
            'LotSize': [75, 75, 75, 75, 75, 75, 75, 75, 75, 75],
            'QtyLimit': [1800, 1800, 1800, 1800, 1800, 1800, 1800, 1800, 1800, 1800],
            'ScripCode': [35004, 35005, 35008, 35009, 35219, 35220, 35577, 35592, 35679, 35681]
        }
        df = pd.DataFrame(sample_data)

# --- OrderBookManager Class ---
class OrderBookManager:
    def __init__(self, file_name="order_book.json", max_size=1000):
        self.order_book = []
        self.order_book_file = file_name
        self.max_order_book_size = max_size
        self.current_total_quantity = 0
        self.current_average_price = 0.0
        self.buy_count = 0
        self.sell_count = 0
        self.realized_pnl = 0.0
        self._lock = threading.Lock()
        self._load_persisted_data()

    def _load_persisted_data(self):
        with self._lock:
            if os.path.exists(self.order_book_file):
                try:
                    with open(self.order_book_file, "r") as file:
                        data = json.load(file)
                        self.order_book = data.get('order_book', [])
                        self.current_total_quantity = data.get('current_total_quantity', 0)
                        self.current_average_price = data.get('current_average_price', 0.0)
                        self.buy_count = data.get('buy_count', 0)
                        self.sell_count = data.get('sell_count', 0)
                        self.realized_pnl = data.get('realized_pnl', 0.0)
                    logging.info(f"Loaded {len(self.order_book)} orders and portfolio state from {self.order_book_file}")
                except (IOError, json.JSONDecodeError) as e:
                    logging.error(f"Error loading {self.order_book_file}: {e}. Initializing fresh state.")
                    self._reset_state()
            else:
                logging.info(f"No order book file found. Initializing fresh state.")
                self._reset_state()

    def _reset_state(self):
        self.order_book = []
        self.current_total_quantity = 0
        self.current_average_price = 0.0
        self.buy_count = 0
        self.sell_count = 0
        self.realized_pnl = 0.0

    def _save_persisted_data(self):
        with self._lock:
            try:
                data = {
                    'order_book': self.order_book,
                    'current_total_quantity': self.current_total_quantity,
                    'current_average_price': self.current_average_price,
                    'buy_count': self.buy_count,
                    'sell_count': self.sell_count,
                    'realized_pnl': self.realized_pnl
                }
                with open(self.order_book_file, "w") as file:
                    json.dump(data, file, indent=4)
            except IOError as e:
                logging.error(f"Error saving order book to {self.order_book_file}: {e}")

    def add_order(self, order_details):
        with self._lock:
            self.order_book.append(order_details)
            self.order_book[:] = self.order_book[-self.max_order_book_size:]
            logging.info(f"Order added to book: {order_details.get('order_id', 'N/A')}")
            self._save_persisted_data()

    def update_position_and_pnl(self, order_details, current_ltp):
        with self._lock:
            order_side = order_details['side']
            quantity = order_details['quantity']
            price = order_details['price']
            if order_side == "BUY":
                total_amount_new_buy = quantity * price
                current_total_amount = self.current_total_quantity * self.current_average_price
                self.current_total_quantity += quantity
                self.current_average_price = (current_total_amount + total_amount_new_buy) / self.current_total_quantity if self.current_total_quantity > 0 else 0.0
                self.buy_count += 1
                logging.info(f"Position after BUY: Qty={self.current_total_quantity}, AvgPrice={self.current_average_price:.2f}")
            elif order_side == "SELL":
                if self.current_total_quantity > 0:
                    sold_quantity = min(quantity, self.current_total_quantity)
                    pnl_from_sell = (price - self.current_average_price) * sold_quantity
                    self.realized_pnl += pnl_from_sell
                    logging.info(f"Realized PnL from SELL: {pnl_from_sell:.2f}")
                    self.current_total_quantity -= sold_quantity
                    self.sell_count += 1
                    if self.current_total_quantity == 0:
                        logging.info("Position fully closed. Resetting counts.")
                        self.current_average_price = 0.0
                        self.buy_count = 0
                        self.sell_count = 0
                logging.info(f"Position after SELL: Qty={self.current_total_quantity}, Realized PnL={self.realized_pnl:.2f}")
            self._save_persisted_data()

    def get_all_orders(self):
        with self._lock:
            return list(self.order_book)

    def calculate_pnl(self, current_ltp: float) -> dict:
        with self._lock:
            unrealized_pnl = 0.0
            if self.current_total_quantity > 0:
                unrealized_pnl = (current_ltp - self.current_average_price) * self.current_total_quantity
            net_pnl = self.realized_pnl + unrealized_pnl
            return {
                'realized_pnl': self.realized_pnl,
                'unrealized_pnl': unrealized_pnl,
                'net_pnl': net_pnl,
                'current_total_quantity': self.current_total_quantity,
                'current_average_price': self.current_average_price
            }

# --- Core Trading Logic Functions ---
def append_ltp_to_history(scrip_code: int, ltp: float, max_history_size: int = 300):
    if not isinstance(ltp, (int, float)) or ltp <= 0 or not isinstance(scrip_code, int):
        return
    scrip_code_str = str(scrip_code)
    if scrip_code_str not in in_memory_ltp_history:
        in_memory_ltp_history[scrip_code_str] = deque(maxlen=max_history_size)
    in_memory_ltp_history[scrip_code_str].append(float(ltp))

def calculate_ltp_metrics(scrip_code: int, current_ltp: float):
    scrip_code_str = str(scrip_code)
    ltp_list = in_memory_ltp_history.get(scrip_code_str, [])
    if not ltp_list:
        return {'high': None, 'low': None, 'avg_ltp': None}
    return {
        'high': max(ltp_list),
        'low': min(ltp_list),
        'avg_ltp': sum(ltp_list) / len(ltp_list)
    }

def get_ltp(scrip_code: int, config: dict) -> dict | None:
    EXCH = "B" if config['exchange'] == "BSE" else "N"
    url = "https://openapi.5paisa.com/VendorsAPI/Service1.svc/V1/MarketFeed"
    payload = {
        "head": {"key": YOUR_5PAISA_API_KEY},
        "body": {
            "MarketFeedData": [{"Exch": EXCH, "ExchType": "D", "ScripCode": scrip_code}],
            "LastRequestTime": "/Date(0)/",
            "RefreshRate": "H"
        }
    }
    
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retries))
    
    try:
        logging.info(f"Fetching LTP for scrip {scrip_code} from {url}")
        response = session.post(
            url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=5
        )
        response.raise_for_status()
        data = response.json()
        ltp = data.get("body", {}).get("Data", [{}])[0].get("LastRate")
        if ltp is not None and ltp > 0:
            ltp_value = float(ltp)
            logging.info(f"Fetched LTP for scrip {scrip_code}: {ltp_value}")
            append_ltp_to_history(scrip_code, ltp_value)
            metrics = calculate_ltp_metrics(scrip_code, ltp_value)
            return {'current_ltp': ltp_value, **metrics}
        else:
            logging.error(f"No valid LTP found in response: {data}")
            return None
    except requests.RequestException as e:
        logging.error(f"Error fetching LTP for scrip {scrip_code}: {str(e)}. Response: {e.response.text if e.response else 'No response'}")
        return None
    except (json.JSONDecodeError, IndexError, TypeError) as e:
        logging.error(f"Error parsing LTP response for scrip {scrip_code}: {str(e)}. Response: {data if 'data' in locals() else 'No data'}")
        return None

def place_order(scrip_code: int, quantity: int, order_side: str, config: dict, order_book_manager: OrderBookManager) -> bool:
    if config.get('dummy_mode', False):
        logging.info(f"Dummy mode enabled: Simulating {order_side} order for {quantity} units of scrip {scrip_code}.")
        ltp_result = get_ltp(scrip_code, config)
        ltp = ltp_result['current_ltp'] if ltp_result and 'current_ltp' in ltp_result else 0
        if ltp == 0:
            logging.error(f"Could not get LTP for dummy order. Aborting {order_side} order.")
            return False
        order = {
            'order_id': f"DUMMY_ORD_{int(time() * 1000)}",
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'side': order_side,
            'quantity': quantity,
            'price': ltp,
            'status': 'Completed'
        }
        order_book_manager.add_order(order)
        order_book_manager.update_position_and_pnl(order, ltp)
        logging.info(f"Dummy {order_side} order simulated for {quantity} units at {ltp}. Order ID: {order['order_id']}")
        return True

    ltp_result = get_ltp(scrip_code, config)
    ltp = ltp_result['current_ltp'] if ltp_result and 'current_ltp' in ltp_result else 0
    if ltp == 0:
        logging.error(f"Could not get LTP for order placement. Aborting {order_side} order.")
        return False

    order = {
        'order_id': f"ORD_{int(time() * 1000)}",
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'side': order_side,
        'quantity': quantity,
        'price': ltp,
        'status': 'Completed'
    }

    try:
        with open("access_token.json", "r") as file:
            access_token = json.load(file).get("access_token")
        if not access_token:
            logging.error("Access token not found in access_token.json.")
            return False
    except (FileNotFoundError, json.JSONDecodeError):
        logging.error("access_token.json not found or is invalid.")
        return False

    STOCKO_EXCHANGE = "BFO" if config['exchange'] == "BSE" else "NFO"
    url = "https://api.stocko.in/api/v1/orders"
    order_data = {
        "exchange": STOCKO_EXCHANGE, "order_type": "MARKET", "instrument_token": scrip_code,
        "quantity": quantity, "disclosed_quantity": 0, "price": 0, "order_side": order_side,
        "trigger_price": 0, "validity": "DAY", "product": "MIS", "client_id": "YOUR_CLIENT_ID",
        "user_order_id": int(time() * 1000), "market_protection_percentage": 0, "device": "WEB"
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}
    
    try:
        response = requests.post(url, headers=headers, data=json.dumps(order_data), timeout=10)
        response.raise_for_status()
        response_json = response.json()
        if response.status_code == 200 and response_json.get("status") == "success":
            order['order_id'] = response_json.get('order_id', order['order_id'])
            order_book_manager.add_order(order)
            logging.info(f"{order_side} order placed for {quantity} units at {ltp}. Order ID: {order['order_id']}")
            order_book_manager.update_position_and_pnl(order, ltp)
            return True
        else:
            logging.error(f"Failed to place order: {response.text}")
            return False
    except requests.RequestException as e:
        logging.error(f"API error placing order: {e}")
        return False

def calculate_Qty(quantity_to_trade: int, Buy_Count: int) -> int:
    multipliers = {
        (0, 4): 1, (5, 9): 2, (10, 14): 5, (15, 19): 10,
        (20, 29): 20, (30, 39): 25
    }
    for (start, end), multiplier in multipliers.items():
        if start <= Buy_Count <= end:
            return quantity_to_trade * multiplier
    return quantity_to_trade * 25

def execute_trading_strategy(config: dict, order_book_manager: OrderBookManager):
    scrip_code = config['scrip_code']
    LOT_SIZE = config['lot_size']
    INTITIAL_QUANTITY = config['initial_quantity']

    logging.info(f"--- Executing strategy for scrip {scrip_code} ---")
    ltp_metrics = get_ltp(scrip_code, config)
    if not ltp_metrics or not ltp_metrics.get('current_ltp'):
        logging.warning("Could not get LTP. Skipping strategy cycle.")
        return

    current_ltp = ltp_metrics['current_ltp']
    avg_ltp = ltp_metrics['avg_ltp']
    high = ltp_metrics['high']
    low = ltp_metrics['low']

    portfolio = order_book_manager.calculate_pnl(current_ltp)
    qty = portfolio['current_total_quantity']
    avg_price = portfolio['current_average_price']
    buy_count = order_book_manager.buy_count
    sell_count = order_book_manager.sell_count

    if qty == 0:
        if avg_ltp and high and current_ltp > avg_ltp and current_ltp < (high * 0.98):
             quantity_to_buy = (INTITIAL_QUANTITY // LOT_SIZE) * LOT_SIZE
             if quantity_to_buy > 0:
                 logging.info(f"Initial Buy condition met! Buying {quantity_to_buy} units.")
                 place_order(scrip_code, quantity_to_buy, "BUY", config, order_book_manager)
        else:
            logging.info(f"No initial buy condition met. LTP:{current_ltp:.2f} AvgLTP:{avg_ltp or 'N/A'}")
        return

    profit_targets = {0: 1.03, 1: 1.05, 2: 1.07}
    
    if sell_count in profit_targets and current_ltp >= avg_price * profit_targets[sell_count]:
        logging.info(f"Profit target {sell_count + 1} met!")
        if sell_count < 2:
             quantity_to_sell = math.floor((qty * 0.5) / LOT_SIZE) * LOT_SIZE
        else:
            quantity_to_sell = qty
        
        if quantity_to_sell > 0:
            logging.info(f"Attempting to SELL {quantity_to_sell} units.")
            place_order(scrip_code, quantity_to_sell, "SELL", config, order_book_manager)
        else:
            logging.info("Calculated sell quantity is zero, cannot place order.")
        return

    if buy_count >= 40:
        logging.info(f"Max buy count (40) reached. No more averaging down.")
        return
        
    price_drop_threshold = 0.95
    if current_ltp < avg_price * price_drop_threshold:
        calculated_qty_for_buy = calculate_Qty(INTITIAL_QUANTITY, buy_count)
        quantity_to_buy = max(0, (calculated_qty_for_buy // LOT_SIZE) * LOT_SIZE)
        if quantity_to_buy > 0:
            logging.info(f"Averaging down condition met! Buying {quantity_to_buy} units.")
            place_order(scrip_code, quantity_to_buy, "BUY", config, order_book_manager)
        else:
             logging.info("Calculated buy quantity for averaging is zero.")
        return

    logging.info("No trading condition met in this cycle.")

def trading_loop(stop_event: threading.Event):
    logging.info("Trading loop started.")
    while not stop_event.is_set():
        try:
            with APP_STATE["lock"]:
                config = APP_STATE["config"]
                manager = APP_STATE["order_book_manager"]
            
            if config and manager:
                execute_trading_strategy(config, manager)
            else:
                logging.warning("Bot running but not configured. Waiting for start signal.")
        except Exception as e:
            logging.error(f"FATAL ERROR in trading loop: {e}", exc_info=True)
        stop_event.wait(2)
    
    logging.info("Trading loop has been stopped.")

# --- Flask Application ---
app = Flask(__name__)
with open("index.html", "r") as f:
    HTML_TEMPLATE = f.read()

@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

@app.route('/get_expiry_dates')
def get_expiry_dates():
    """Get unique expiry dates based on selected exchange"""
    exchange = request.args.get('exchange')
    
    if df is None:
        return jsonify({'error': 'No data available'}), 500
    
    filtered_df = df[df['Exch'] == exchange]
    expiry_dates = sorted(pd.to_datetime(filtered_df['Expiry']).dt.strftime('%m/%d/%Y').unique().tolist())

    return jsonify(expiry_dates)

@app.route('/get_scrip_types')
def get_scrip_types():
    """Get unique scrip types based on exchange and expiry"""
    exchange = request.args.get('exchange')
    expiry = request.args.get('expiry')
    
    if df is None:
        return jsonify({'error': 'No data available'}), 500
    
    # Normalize date format for comparison
    expiry_dt = pd.to_datetime(expiry).strftime('%Y-%m-%d')
    df_expiry_dt = pd.to_datetime(df['Expiry']).dt.strftime('%Y-%m-%d')

    filtered_df = df[(df['Exch'] == exchange) & (df_expiry_dt == expiry_dt)]
    scrip_types = sorted(filtered_df['ScripType'].unique().tolist())
    
    return jsonify(scrip_types)

@app.route('/get_strike_rates')
def get_strike_rates():
    """Get unique strike rates based on exchange, expiry, and scrip type"""
    exchange = request.args.get('exchange')
    expiry = request.args.get('expiry')
    scrip_type = request.args.get('scrip_type')
    
    if df is None:
        return jsonify({'error': 'No data available'}), 500

    expiry_dt = pd.to_datetime(expiry).strftime('%Y-%m-%d')
    df_expiry_dt = pd.to_datetime(df['Expiry']).dt.strftime('%Y-%m-%d')
    
    filtered_df = df[
        (df['Exch'] == exchange) & 
        (df_expiry_dt == expiry_dt) & 
        (df['ScripType'] == scrip_type)
    ]
    strike_rates = sorted(filtered_df['StrikeRate'].unique().tolist())
    
    return jsonify(strike_rates)

@app.route('/get_script_details')
def get_script_details():
    """Get script details based on all filters"""
    exchange = request.args.get('exchange')
    expiry = request.args.get('expiry')
    scrip_type = request.args.get('scrip_type')
    strike_rate = request.args.get('strike_rate')
    
    if df is None:
        return jsonify({'error': 'No data available'}), 500
    
    try:
        strike_rate = float(strike_rate)
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid strike rate'}), 400

    expiry_dt = pd.to_datetime(expiry).strftime('%Y-%m-%d')
    df_expiry_dt = pd.to_datetime(df['Expiry']).dt.strftime('%Y-%m-%d')
    
    filtered_df = df[
        (df['Exch'] == exchange) & 
        (df_expiry_dt == expiry_dt) & 
        (df['ScripType'] == scrip_type) & 
        (df['StrikeRate'] == strike_rate)
    ]
    
    if filtered_df.empty:
        return jsonify({'error': 'No matching record found'}), 404
    
    record = filtered_df.iloc[0]
    
    return jsonify({
        'lot_size': int(record['LotSize']),
        'qty_limit': int(record['QtyLimit']),
        'scrip_code': int(record['ScripCode']),
        'name': record['Name']
    })

@app.route('/start', methods=['POST'])
def start_bot():
    with APP_STATE["lock"]:
        if APP_STATE["bot_thread"] and APP_STATE["bot_thread"].is_alive():
            return jsonify({"status": "error", "message": "Bot is already running."}), 400

        config = request.json
        logging.info(f"Received start command with config: {config}")

        # Validate required fields
        required_fields = ['scrip_code', 'exchange', 'lot_size', 'initial_quantity']
        for field in required_fields:
            if field not in config or not config[field]:
                return jsonify({"status": "error", "message": f"Missing or empty field: {field}"}), 400

        try:
            scrip_code = int(config['scrip_code'])
            lot_size = int(config['lot_size'])
            initial_quantity = int(config['initial_quantity'])
            exchange = config['exchange']
            dummy_mode = config.get('dummy_mode', False)
        except (ValueError, TypeError):
            return jsonify({"status": "error", "message": "Invalid numeric values for scrip_code, lot_size, or initial_quantity"}), 400

        if exchange not in ['NSE', 'BSE']:
            return jsonify({"status": "error", "message": "Invalid exchange value"}), 400

        if scrip_code <= 0 or lot_size <= 0 or initial_quantity <= 0:
            return jsonify({"status": "error", "message": "Scrip code, lot size, and initial quantity must be positive"}), 400

        APP_STATE["logs"].clear()
        in_memory_ltp_history.clear()
        last_buy_price_for_scrip.clear()
        
        APP_STATE["config"] = {
            'scrip_code': scrip_code,
            'exchange': exchange,
            'lot_size': lot_size,
            'initial_quantity': initial_quantity,
            'dummy_mode': dummy_mode
        }
        APP_STATE["order_book_manager"] = OrderBookManager(file_name=f"order_book_{scrip_code}.json")
        APP_STATE["stop_event"].clear()
        APP_STATE["bot_thread"] = threading.Thread(target=trading_loop, args=(APP_STATE["stop_event"],), daemon=True)
        APP_STATE["bot_thread"].start()

        logging.info("Trading bot thread started successfully.")
        return jsonify({"status": "success", "message": "Bot started successfully."})

@app.route('/stop', methods=['POST'])
def stop_bot():
    with APP_STATE["lock"]:
        if not APP_STATE["bot_thread"] or not APP_STATE["bot_thread"].is_alive():
            return jsonify({"status": "error", "message": "Bot is not running."}), 400

        logging.info("Received stop command.")
        APP_STATE["stop_event"].set()
        APP_STATE["bot_thread"].join(timeout=5)
        if APP_STATE["bot_thread"].is_alive():
            logging.warning("Bot thread did not stop gracefully within 5 seconds.")
        else:
            logging.info("Bot thread stopped successfully.")
        APP_STATE["bot_thread"] = None
        APP_STATE["order_book_manager"] = None
        APP_STATE["config"] = {}
        return jsonify({"status": "success", "message": "Bot stopped."})

@app.route('/status')
def get_status():
    with APP_STATE["lock"]:
        if not APP_STATE["order_book_manager"]:
            return jsonify({"bot_running": False})
        manager = APP_STATE["order_book_manager"]
        config = APP_STATE["config"]
    
    ltp_metrics = get_ltp(config['scrip_code'], config)
    current_ltp = ltp_metrics['current_ltp'] if ltp_metrics else 0.0

    # Look up scrip name from the dataframe
    scrip_name = "N/A"
    if df is not None:
        record = df[df['ScripCode'] == config['scrip_code']]
        if not record.empty:
            scrip_name = record.iloc[0]['Name']
    
    pnl_data = manager.calculate_pnl(current_ltp)
    return jsonify({
        "bot_running": True,
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "config": {
            "scrip_code": config['scrip_code'],
            "scrip_name": scrip_name,
            "initial_quantity": config['initial_quantity']
        },
        "current_ltp": current_ltp,
        "portfolio_status": pnl_data,
        "buy_count": manager.buy_count,
        "sell_count": manager.sell_count,
    })

@app.route('/orders')
def get_orders():
    with APP_STATE["lock"]:
        if not APP_STATE["order_book_manager"]:
            return jsonify({"orders": []})
        orders = APP_STATE["order_book_manager"].get_all_orders()
    return jsonify({
        "total_orders": len(orders),
        "orders": orders
    })

@app.route('/logs')
def get_logs():
    with APP_STATE["lock"]:
        logs = list(APP_STATE["logs"])
    return jsonify({"logs": logs})

if __name__ == "__main__":
    load_csv_data()
    app.run(host='0.0.0.0', port=5000, debug=True)
