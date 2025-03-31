import logging
import json # <<< Added
import os   # <<< Added

# --- Logging Setup ---
log_file = 'simulation.log'
logging.basicConfig(level=logging.DEBUG, # Keep DEBUG for now
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    filename=log_file,
                    filemode='w')
# Optional Console Handler (set level as needed)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
# logging.getLogger('').addHandler(console_handler) # Uncomment for console logs

# --- VVV Load Global LLM Config VVV ---
CONFIG_FILE = 'config.json'
LLM_CONFIGS = []
LLM_TIMEOUT = 20 # Default timeout, can be overridden by config file

try:
    logging.info(f"Attempting to load configuration from {CONFIG_FILE}...")
    with open(CONFIG_FILE, 'r') as f:
        config_data = json.load(f)
        LLM_CONFIGS = config_data.get('llm_configs', [])
        LLM_TIMEOUT = config_data.get('llm_timeout', LLM_TIMEOUT)
        # Load API keys from environment variables specified in the config
        api_keys_found = {}
        for i, cfg in enumerate(LLM_CONFIGS):
            key_var = cfg.get('api_key_env_var')
            actual_key = None
            if key_var:
                actual_key = os.environ.get(key_var)
                if actual_key:
                    api_keys_found[key_var] = True
                else:
                    logging.warning(f"API key environment variable '{key_var}' for config '{cfg.get('id', i)}' is NOT SET!")
            cfg['api_key'] = actual_key # Store the key (or None) in the config dict
    logging.info(f"Loaded {len(LLM_CONFIGS)} LLM configurations. Timeout set to {LLM_TIMEOUT}s.")
    if any(cfg.get('api_type') == 'gemini' for cfg in LLM_CONFIGS) and not api_keys_found:
         logging.warning("Gemini configs detected, but no API keys were found in environment variables.")

except FileNotFoundError:
    logging.error(f"FATAL: Configuration file '{CONFIG_FILE}' not found!")
    LLM_CONFIGS = [] # Ensure it's empty so checks fail later
except json.JSONDecodeError:
    logging.error(f"FATAL: Could not parse '{CONFIG_FILE}'! Check JSON format.")
    LLM_CONFIGS = []
except Exception as e:
    logging.error(f"FATAL: Unexpected error loading config: {e}", exc_info=True)
    LLM_CONFIGS = []

# Exit if config loading failed critically
if not LLM_CONFIGS:
    print(f"CRITICAL ERROR: No valid LLM configurations loaded from {CONFIG_FILE}. Please create/fix the file. Exiting.")
    exit()
# --- ^^^ Load Global LLM Config ^^^ ---


# --- REMOVED Old LLM Constants ---
# LLM_API_ENDPOINT = "http://localhost:11434/api/generate"
# LLM_MODEL_NAME = "gemma3:4b"
# LLM_TIMEOUT = 20
LLM_DECISION_FREQUENCY = 5 # how many ticks to respond
# BASE_OLLAMA_PORT = 11434
# --- END REMOVED ---

# Colors (Unchanged)
COLOR_WHITE = (255, 255, 255); COLOR_BLACK = (0, 0, 0); COLOR_GRID = (40, 40, 40)
# COLOR_AGENT removed
COLOR_GROUP_START = (0, 255, 0); COLOR_GROUP_END = (255, 0, 0)
COLOR_RESOURCE = (255, 255, 0); COLOR_TEXT = (200, 200, 200); COLOR_BUTTON = (80, 80, 80)
COLOR_BUTTON_HOVER = (120, 120, 120); COLOR_BUTTON_TEXT = (230, 230, 230)
COLOR_PANEL_BG = (30, 30, 60); COLOR_SELECTED_BORDER = (255, 255, 0) # Yellow

AGENT_INITIAL_COLORS = [ # Unchanged
    (0, 200, 255), (255, 150, 0), (0, 255, 100), (255, 50, 200),
    (150, 100, 255), (200, 255, 0), (255, 0, 0), (0, 0, 255),
    (100, 255, 150), (255, 100, 100) ]

# Display & Grid (Unchanged)
GRID_WIDTH = 20
GRID_HEIGHT = 20
CELL_SIZE = 40
UI_AREA_HEIGHT = 50
INFO_PANEL_WIDTH = 600
SCREEN_WIDTH = GRID_WIDTH * CELL_SIZE
SCREEN_HEIGHT = GRID_HEIGHT * CELL_SIZE + UI_AREA_HEIGHT
TOTAL_WINDOW_WIDTH = SCREEN_WIDTH + INFO_PANEL_WIDTH
FPS = 5

# Simulation Parameters (Unchanged)
INITIAL_AGENTS = 3
INITIAL_RESOURCES = 15
RESOURCE_SPAWN_RATE = 0.02
RESOURCE_COLLECT_AMOUNT = 20
RESOURCE_MAX_QUANTITY = 40
EXPLORE_DURATION_TICKS = 8

# Agent Base Stats (Unchanged)
AGENT_MAX_HP = 100
AGENT_BASE_STRENGTH_RANGE = (5, 15)
AGENT_BASE_DEFENSE_RANGE = (3, 10)
AGENT_BASE_FIGHTING_ABILITY_RANGE = (8, 20)
AGENT_MAX_LOG_ENTRIES = 50
AGENT_HARVEST_RATE_RANGE = (1, 5)
AGENT_CONSUMPTION_RATE = 1
STARVATION_DAMAGE_PER_TICK = 2
AGENT_MAX_RESOURCES = 200
PERCEPTION_RADIUS = 5 # Radius in grid cells
MAX_TRAIL_LENGTH = 15 # How many steps the agent remembers/renders
TRAIL_FADE_COLOR = COLOR_BLACK # Color to fade trail towards (e.g., background color)

# --- Add Enums needed by multiple files? Or keep in helper.py ---
# It's better practice to have Enums where they are primarily used or in a shared types file.
# Let's assume they remain in helper.py for now based on the provided structure.