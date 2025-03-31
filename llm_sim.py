# -*- coding: utf-8 -*-
import logging
from simulation import Simulation

# --- Main Execution ---
if __name__ == '__main__':
    # Logging is configured in constants.py now
    # Optional: Endpoint check removed as multiple endpoints are used
    # print(f"Checking LLM endpoint {LLM_API_ENDPOINT}...") # No single endpoint anymore
    logging.info("--- Simulation Start ---")
    sim = Simulation() # Simulation __init__ handles config loading and warnings
    sim.run()
    logging.info("--- Simulation End ---")