from constants import *
from enum import Enum
from collections import deque # For efficient agent history logs
import requests
import json     # Needed for make_llm_api_call
import time

# --- Enums and Helper Functions ---
class ActionType(Enum):
    IDLE = 0
    MOVE = 1
    COLLECT_RESOURCE = 2
    ATTACK_GROUP = 3
    FORM_GROUP = 4
    ACCEPT_GROUP = 5
    ATTACK_AGENT = 6

class PlanType(Enum):
    IDLE = 0
    GO_TO_POS = 1
    GO_TO_RESOURCE = 2
    GO_TO_AGENT = 3
    FORM_GROUP_WITH = 5
    ACCEPT_GROUP_FROM = 6
    ATTACK_TARGET = 7
    EXPLORE = 8
    WAITING_LLM = 9 # (COLLECT_HERE removed, HARVESTING removed)
    RESPOND_TO_GROUP_REQUEST = 11 # Special state to prioritize group decision LLM call
    WAITING_GROUP_RESPONSE = 12 # State for the agent who initiated the request
    
def manhattan_distance(pos1, pos2):
    """Calculates Manhattan distance between two (x, y) tuples."""
    return abs(pos1[0] - pos2[0]) + abs(pos1[1] - pos2[1])

# --- VVV Agent Log File Handling VVV ---
_agent_log_files = {} # Cache for agent-specific file handles: {agent_id: file_handle}
_agent_log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s') # Optional: Use logging formatter style

def log_agent_event(agent_id, message, agent_ref=None, level=logging.INFO):
    """Logs to main file, agent-specific file, and optionally internal history."""
    # 1. Log to main simulation.log via root logger
    root_logger_message = f"Agent {agent_id}: {message}"
    logging.log(level, root_logger_message)

    # 2. Log to agent-specific file (e.g., agent0.log)
    try:
        if agent_id not in _agent_log_files:
            filename = f"agent{agent_id}.log"
            # Open in 'w' (write mode) to clear the file each time the simulation starts
            _agent_log_files[agent_id] = open(filename, 'w', encoding='utf-8')
            logging.info(f"Opened log file {filename} for Agent {agent_id}") # Log file opening to main log

        file_handle = _agent_log_files[agent_id]

        # Format the message for the agent file
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        ms = f"{time.time()%1:.3f}"[1:] # Milliseconds part
        level_name = logging.getLevelName(level)
        # Manually format like the basic config formatter
        log_line = f"{timestamp},{ms[1:]} - {level_name} - {message}\n" # Use message directly, it's already agent-specific context

        file_handle.write(log_line)
        file_handle.flush() # Ensure it's written immediately for debugging
    except Exception as e:
        logging.error(f"Error writing to agent log file for Agent {agent_id}: {e}")

    # 3. Append to agent's internal history deque (if agent_ref provided)
    if agent_ref and hasattr(agent_ref, 'history_log'):
        time_step_str = f"T{agent_ref.simulation_time_step}: " if hasattr(agent_ref, 'simulation_time_step') else ""
        # Only add the core message to the internal log, not timestamp/level again
        agent_ref.history_log.append(f"{time_step_str}{message}")

def close_agent_log_files():
    """Closes all open agent-specific log file handles."""
    logging.info("Closing agent-specific log files...")
    count = 0
    for agent_id, handle in _agent_log_files.items():
        try:
            handle.close()
            count += 1
        except Exception as e:
            logging.error(f"Error closing log file for Agent {agent_id}: {e}")
    _agent_log_files.clear() # Clear the cache
    logging.info(f"Closed {count} agent log files.")
# --- ^^^ Agent Log File Handling ^^^ ---

# --- LLM Worker Thread Function (Modified) ---
def llm_worker(request_q, result_q, agent_manager): # <<< Pass agent_manager
    """Processes LLM requests using agent-specific configs via make_llm_api_call."""
    logging.info("LLM worker thread started.")
    while True:
        agent_id = None
        try:
            agent_id, context = request_q.get() # Blocks until item available
            if agent_id is None: logging.info("LLM worker stop signal."); break # Stop signal

            # Get agent using manager
            agent = agent_manager.get_agent(agent_id)
            if not agent or not agent.is_alive(): # Check if agent still exists and is alive
                logging.warning(f"LLM worker: Agent {agent_id} not found or dead. Skipping request.")
                result_q.put((agent_id, None)); continue # Signal failure back
            if not agent.llm_config: # Check if agent has config assigned
                logging.warning(f"LLM worker: Agent {agent_id} missing LLM config. Skipping request.")
                result_q.put((agent_id, None)); continue

            logging.debug(f"LLM worker processing for Agent {agent_id} using config {agent.llm_config.get('id')}")

            # --- VVV Use new API call function VVV ---
            # This function now handles API type switching, headers, body, parsing etc.
            parsed_decision_dict = make_llm_api_call(agent_id, context, agent.llm_config)
            # --- ^^^ Use new API call function ^^^ ---

            # Put the parsed dictionary (or None) onto the result queue
            result_q.put((agent_id, parsed_decision_dict))
            logging.debug(f"LLM worker finished processing for Agent {agent_id}")

        except Exception as e: # Catch errors in the worker loop itself
            logging.error(f"Error in LLM worker thread loop (Agent {agent_id}): {e}", exc_info=True)
            if agent_id is not None: # Try to signal failure if we know the agent ID
                 try: result_q.put((agent_id, None))
                 except Exception as qe: logging.error(f"Error putting fail result on queue: {qe}")
        finally:
            # No task_done needed for basic queue.Queue
            pass

    logging.info("LLM worker thread stopped.")


def make_llm_api_call(agent_id, context_prompt, agent_llm_config):
    """
    Makes API call based on agent's config (handles different API types).
    Returns the *parsed JSON* plan dictionary, or None on failure.
    """
    if not agent_llm_config:
        log_agent_event(agent_id, "LLM call failed: Agent has no LLM config.", level=logging.ERROR)
        return None

    # Extract details from the specific agent's config
    api_type = agent_llm_config.get('api_type', 'unknown').lower()
    endpoint = agent_llm_config.get('endpoint')
    api_key = agent_llm_config.get('api_key') # Key should be loaded from env var previously
    model_name = agent_llm_config.get('model_name')
    timeout = LLM_TIMEOUT # Use global timeout from constants (loaded from config)

    if not endpoint:
        log_agent_event(agent_id, f"LLM call failed: Missing 'endpoint' in config ID {agent_llm_config.get('id')}.", level=logging.ERROR)
        return None

    headers = {'Content-Type': 'application/json'}
    payload = {}
    request_endpoint = endpoint # Use original endpoint by default
    response_text = None

    log_agent_event(agent_id, f"Preparing {api_type} request to {endpoint}...", level=logging.DEBUG)

    try:
        # --- Prepare request based on API type ---
        if api_type == 'ollama':
            # Assume Ollama server expects model name in payload
            payload = {
                "model": model_name,
                "prompt": context_prompt,
                "stream": False,
                "format": "json", # Request direct JSON output if supported
                "options": {"temperature": 0.7}
            }
            # Log prompt before sending
            log_agent_event(agent_id, f"Agent {agent_id} (Ollama) - Sending Prompt:\n-------START PROMPT-------\n{context_prompt}\n-------END PROMPT-------")

        elif api_type == 'gemini':
            if not api_key:
                log_agent_event(agent_id, f"LLM call failed: Missing API key for Gemini config ID {agent_llm_config.get('id')}.", level=logging.ERROR)
                return None
            # Add API key to endpoint URL
            request_endpoint = f"{endpoint}?key={api_key}"
            # Gemini API structure expects specific JSON format
            payload = {
                "contents": [{
                    "parts": [{"text": context_prompt}] # The actual prompt goes here
                }],
                "generationConfig": {
                    "temperature": 0.7,
                    # Crucial: Tell Gemini we want JSON output if the model supports it
                    "responseMimeType": "application/json",
                },
                 # IMPORTANT: Include safety settings to avoid blocks for common content
                "safetySettings": [
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                ]
            }
             # Log prompt before sending
            log_agent_event(agent_id, f"Agent {agent_id} (Gemini) - Sending Prompt:\n-------START PROMPT-------\n{context_prompt}\n-------END PROMPT-------")

        # TODO: Add elif blocks for 'openai' or other API types if needed
        # Example for OpenAI-compatible:
        # elif api_type == 'openai':
        #    if not api_key: # OpenAI key needed
        #         log_agent_event(agent_id, f"LLM call failed: Missing API key for OpenAI config...", level=logging.ERROR); return None
        #    headers['Authorization'] = f"Bearer {api_key}"
        #    payload = {
        #        "model": model_name,
        #        "messages": [{"role": "user", "content": context_prompt}],
        #        "temperature": 0.7,
        #        "response_format": { "type": "json_object" } # Request JSON mode
        #    }
        #    logging.info(f"Agent {agent_id} (OpenAI) - Sending Prompt:\n{context_prompt}")

        else:
            log_agent_event(agent_id, f"LLM call failed: Unknown api_type '{api_type}' in config.", level=logging.ERROR)
            return None

        # --- Make API Call ---
        response = requests.post(request_endpoint, headers=headers, json=payload, timeout=timeout)
        # --- Log Raw Status/Response Text ---
        log_agent_event(agent_id, f"Agent {agent_id} - Received Raw Status Code: {response.status_code}")
        try:
            # Try to log response body, but handle cases where it might not be JSON
            raw_response_body = response.text # Get raw text first
            log_agent_event(agent_id, f"Agent {agent_id} - Received Raw Response Body: {raw_response_body}")
        except Exception as log_err:
            log_agent_event(agent_id, f"Agent {agent_id} - Could not log raw response body: {log_err}")

        response.raise_for_status() # Check for HTTP errors AFTER logging raw response
        response_json = response.json() # Now parse JSON

        # --- Parse response based on API type ---
        if api_type == 'ollama':
            # Ollama with format:json often puts the JSON *string* in 'response'
            response_text = response_json.get('response')
            if not response_text:
                 log_agent_event(agent_id, f"Ollama response missing 'response' field in JSON: {response_json}", level=logging.ERROR); return None
        elif api_type == 'gemini':
            # Check for safety blocks first
            if not response_json.get('candidates'):
                 finish_reason = response_json.get('promptFeedback', {}).get('blockReason', 'Unknown reason')
                 safety_ratings = response_json.get('promptFeedback', {}).get('safetyRatings', [])
                 log_agent_event(agent_id, f"Gemini response blocked. Reason: {finish_reason}, Ratings: {safety_ratings}", level=logging.WARNING)
                 return None
            # Extract text part - assuming responseMimeType worked and it's JSON directly
            # or the JSON string we need is within the text part
            try:
                # Access nested structure carefully
                part = response_json['candidates'][0]['content']['parts'][0]
                if 'text' in part:
                     response_text = part['text']
                else: # Should not happen with JSON mimetype request, but maybe handle function calls later?
                     log_agent_event(agent_id, f"Gemini response part missing 'text': {part}", level=logging.ERROR); return None
            except (IndexError, KeyError, TypeError) as e:
                 log_agent_event(agent_id, f"Error parsing Gemini response structure: {e}. Response: {response_json}", level=logging.ERROR); return None

        # elif api_type == 'openai':
        #    try:
        #         response_text = response_json['choices'][0]['message']['content']
        #    except (IndexError, KeyError, TypeError) as e:
        #         log_agent_event(agent_id, f"Error parsing OpenAI response structure: {e}. Response: {response_json}", level=logging.ERROR); return None

        else: # Should have been caught earlier
             return None

        # --- Final JSON Parsing of the Extracted Text ---
        if response_text:
            try:
                # Clean potential markdown fences around JSON
                cleaned_text = response_text.strip()
                if cleaned_text.startswith("```json"): cleaned_text = cleaned_text[7:]
                if cleaned_text.endswith("```"): cleaned_text = cleaned_text[:-3]
                cleaned_text = cleaned_text.strip()

                # Attempt to parse the cleaned text as JSON
                decision_dict = json.loads(cleaned_text)
                log_agent_event(agent_id, f"Agent {agent_id} - Successfully Parsed Decision Dict: {decision_dict}")
                return decision_dict # Return the parsed dictionary
            except json.JSONDecodeError as e:
                log_agent_event(agent_id, f"LLM final JSON Decode Error: {e}. Text after cleaning was: '{cleaned_text}'", level=logging.ERROR)
                return None # Indicate failure
        else:
             log_agent_event(agent_id, f"Could not extract text response part from {api_type} result.", level=logging.ERROR)
             return None

    # --- Handle Network/Timeout Errors ---
    except requests.exceptions.Timeout:
         log_agent_event(agent_id, f"LLM API Timeout to {endpoint} after {timeout}s.", level=logging.ERROR)
    except requests.exceptions.RequestException as e:
        log_agent_event(agent_id, f"LLM API Request Error to {endpoint}: {e}", level=logging.ERROR)
    except Exception as e: # Catch any other unexpected errors during the process
        log_agent_event(agent_id, f"Unexpected error during LLM API call/processing: {e}", level=logging.ERROR, exc_info=True)

    return None # Return None indicates failure
# --- END NEW API Call Function ---

def interpolate_color(color1, color2, factor):
    """Linearly interpolates between two RGB colors."""
    # factor = 0.0 -> color1, factor = 1.0 -> color2
    factor = max(0.0, min(1.0, factor)) # Clamp factor
    r = int(color1[0] * (1 - factor) + color2[0] * factor)
    g = int(color1[1] * (1 - factor) + color2[1] * factor)
    b = int(color1[2] * (1 - factor) + color2[2] * factor)
    return (r, g, b)