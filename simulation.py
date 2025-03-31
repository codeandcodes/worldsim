import pygame
import time
import logging
import threading # <<< Added
import queue     # <<< Added
from constants import *
from helper import *
from agent import Agent
from grid_manager import GridManager
from combat_manager import CombatManager
from group_manager import GroupManager
from agent_manager import AgentManager
from resource_manager import ResourceManager

# --- Simulation Class ---

class Simulation:
    def __init__(self):
        pygame.init(); pygame.font.init()
        self.screen = pygame.display.set_mode((TOTAL_WINDOW_WIDTH, SCREEN_HEIGHT))
        pygame.display.set_caption("LLM Agent World Simulator")
        self.clock = pygame.time.Clock()
        try: self.font = pygame.font.SysFont(None, 24); self.font_small = pygame.font.SysFont(None, 18)
        except: self.font = pygame.font.Font(None, 24); self.font_small = pygame.font.Font(None, 18); logging.error("Font error.")

        self.is_running = True; self.paused = False; self.time_step = 0
        self.selected_agent_id = None
        self.buttons = {}; self._setup_buttons()

        # --- VVV Use Loaded Global Config VVV ---
        # LLM_CONFIGS is loaded from constants.py now
        self.available_llm_configs = LLM_CONFIGS
        # --- ^^^ Use Loaded Global Config ^^^ ---

        # Queues
        self.llm_request_queue = queue.Queue()
        self.llm_result_queue = queue.Queue()

        # Managers (Pass available configs to AgentManager)
        self.grid_manager = GridManager(GRID_WIDTH, GRID_HEIGHT)
        self.agent_manager = AgentManager(self.grid_manager, self.available_llm_configs) # <<< Pass configs
        self.group_manager = GroupManager(self.agent_manager)
        self.resource_manager = ResourceManager(self.grid_manager)
        self.combat_manager = CombatManager(self.agent_manager, self.group_manager)
        self.agent_manager.combat_manager = self.combat_manager # Link back

        # Start LLM Worker Thread (Pass agent_manager)
        self.llm_thread = threading.Thread(target=llm_worker,
                                           args=(self.llm_request_queue, self.llm_result_queue, self.agent_manager), # <<< Pass agent_manager
                                           daemon=True)
        self.llm_thread.start()

        self._initialize_sim() # Create agents using assigned configs

        logging.info("Simulation initialized.")
        # --- VVV Startup Warnings/Info VVV ---
        if not self.available_llm_configs:
             print("\n*** WARNING: No LLM configurations loaded from config.json. Agents cannot use LLMs. ***\n")
             logging.error("No LLM configs loaded - agents will be unable to get plans.")
        else:
             print(f"\n--- LLM Configuration Summary ---")
             for idx, cfg in enumerate(self.available_llm_configs):
                  print(f"  Agent {idx % len(self.available_llm_configs)} cycle -> Config ID: {cfg.get('id', 'N/A')}, Type: {cfg.get('api_type')}, Endpoint: {cfg.get('endpoint')}")
                  if cfg.get('api_key_env_var') and not cfg.get('api_key'):
                       print(f"    WARNING: API Key Env Var '{cfg['api_key_env_var']}' not set!")
             print(f"---------------------------------")
             if any(cfg.get('api_type') == 'gemini' for cfg in self.available_llm_configs):
                  print("*** REMINDER: Ensure Gemini API keys are set as environment variables! ***\n")
        # --- ^^^ Startup Warnings/Info ^^^ ---


    def _setup_buttons(self):
        """Define positions and properties for UI buttons RELATIVE TO UI AREA."""
        button_h = 30
        # Calculate y position centered vertically within the UI area's height
        button_y = (UI_AREA_HEIGHT - button_h) // 2
        button_w = 100
        x_pos = 10 # x position relative to left edge of UI area

        self.buttons['pause_play'] = {
            # Rect coordinates are now relative to the ui_surface's (0,0) top-left
            'rect': pygame.Rect(x_pos, button_y, button_w, button_h),
            'text': 'Pause', # Initial text
            'active': True   # Always active
        }
        logging.debug(f"Pause/Play button setup relative to UI area at {self.buttons['pause_play']['rect']}")


    def _initialize_sim(self):
        logging.info("Initializing simulation world...")
        for i in range(INITIAL_AGENTS):
             # create_agent now assigns config internally using list from AgentManager
             if not self.agent_manager.create_agent():
                 logging.error(f"Failed to create agent {i+1}/{INITIAL_AGENTS}.")
                 break # Stop trying if no space or no configs
        self.resource_manager.spawn_resources(INITIAL_RESOURCES)
        logging.info(f"World initialized. Agents: {len(self.agent_manager.agents)}, Resources: {len(self.resource_manager.resources)}")

    def run(self): # (Unchanged structure)
        logging.info("Starting simulation loop.")
        while self.is_running:
            start_time = time.time()
            self._handle_input()
            self._process_llm_results() # Process first
            if not self.paused: self._tick() # Run logic if not paused
            self._render() # Always render
            self.clock.tick(FPS)
            # (Performance logging)
        # (Cleanup)
        logging.info("Simulation loop ended. Stopping LLM worker...")
        self.llm_request_queue.put((None, None))
        self.llm_thread.join(timeout=2.0) # Increase timeout slightly
        if self.llm_thread.is_alive(): logging.warning("LLM worker thread did not stop.")
        pygame.quit()
        logging.info("Pygame quit.")

    def _handle_input(self):
        """Processes Pygame events like closing the window and mouse clicks."""
        mouse_pos = pygame.mouse.get_pos() # Get current mouse position

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.is_running = False
                return # Exit event loop immediately on quit

            # Handle Mouse Clicks
            if event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1: # Left mouse button
                    clicked_on_button = False
                    # Check Pause/Play button click
                    button = self.buttons['pause_play']
                    # --- Button Click Check ---
                    # Check if click's Y coordinate is within the UI bar's screen region
                    if mouse_pos[1] >= SCREEN_HEIGHT - UI_AREA_HEIGHT:
                        # Convert mouse Y to be relative to the ui_surface for collision check
                        ui_mouse_pos_y = mouse_pos[1] - (SCREEN_HEIGHT - UI_AREA_HEIGHT)
                        # Use screen X directly (assuming UI bar starts at X=0)
                        ui_mouse_pos = (mouse_pos[0], ui_mouse_pos_y)

                        button = self.buttons['pause_play']
                        # --- DEBUG: Log coordinates and rect for collision check ---
                        logging.debug(f"Click in UI area. Mouse relative: {ui_mouse_pos}, Button rect: {button['rect']}")
                        # Use the button's relative rect and the calculated relative mouse position
                        if button['rect'].collidepoint(ui_mouse_pos):
                             # --- DEBUG: Confirm collision ---
                             logging.debug("Pause/Play button collision DETECTED!")
                             clicked_on_button = True
                             self._handle_button_click('pause_play')
                        # else: # Optional: Log miss
                        #    logging.debug("Click in UI area, but missed button.")

                    # --- End Button Click Check ---

                    # If no button was clicked, check for agent selection click
                    if not clicked_on_button:
                        # Check if click was within the grid area
                        if mouse_pos[0] < SCREEN_WIDTH and mouse_pos[1] < SCREEN_HEIGHT - UI_AREA_HEIGHT:
                            grid_x = mouse_pos[0] // CELL_SIZE
                            grid_y = mouse_pos[1] // CELL_SIZE
                            clicked_agent = None
                            # Find agent at clicked grid cell (check topmost if multiple)
                            objects_at_click = self.grid_manager.get_objects_at(grid_x, grid_y)
                            for obj in reversed(objects_at_click): # Check top object first
                                if isinstance(obj, Agent):
                                     clicked_agent = obj
                                     break

                            if clicked_agent: # An agent was clicked
                                if self.selected_agent_id != clicked_agent.id:
                                     self.selected_agent_id = clicked_agent.id
                                     logging.info(f"Selected Agent {self.selected_agent_id}")
                                # If clicking the same agent again, could deselect? Or keep selected. Current: Keep selected.
                            else: # Clicked on empty space or resource within grid
                                if self.selected_agent_id is not None:
                                     logging.info(f"Deselected Agent {self.selected_agent_id}")
                                     self.selected_agent_id = None # Deselect
                        else: # Clicked outside grid (in UI area or panel), deselect
                             if self.selected_agent_id is not None:
                                 logging.info(f"Deselected Agent {self.selected_agent_id} (clicked outside grid)")
                                 self.selected_agent_id = None


    def _handle_button_click(self, name):
        """Handles the action for the clicked button."""
        # --- DEBUG: Confirm handler invoked ---
        logging.debug(f"Button click handler invoked for: {name}")
        if name == 'pause_play':
            # --- DEBUG: Log state before and after toggle ---
            logging.info(f"Pause/Play TOGGLE: Before: self.paused = {self.paused}")
            self.paused = not self.paused # Toggle pause state
            logging.info(f"Pause/Play TOGGLE: After: self.paused = {self.paused}")
            # Update the button text based on the new state
            self.buttons['pause_play']['text'] = 'Play' if self.paused else 'Pause'
            logging.debug(f"Button text set to: {self.buttons['pause_play']['text']}")


    def _tick(self): # Pass request queue to initiate_llm_requests
        logging.debug(f"--- Tick {self.time_step} Start ---")
        self.agent_manager.update_agent_perception_and_memory(self.time_step)
        # Initiate LLM Requests needs the queue to put requests onto
        self.agent_manager.initiate_llm_requests(self.group_manager, self.resource_manager, self.time_step, self.llm_request_queue)
        self.combat_manager.resolve_all_combats()
        for agent_id in list(self.agent_manager.agents.keys()):
             self.agent_manager.execute_agent_plan_step(agent_id, self.group_manager, self.resource_manager)
        self.agent_manager.apply_consumption()
        self.agent_manager.handle_deaths(self.group_manager)
        self.group_manager.update_all_group_stats()
        self.group_manager.manage_groups()
        self.resource_manager.periodic_spawn()
        logging.debug(f"--- Tick {self.time_step} End ---")
        self.time_step += 1
        if not self.agent_manager.agents: logging.info("All agents died."); self.is_running = False

    def _process_llm_results(self):
        """Processes results from the LLM worker thread queue."""
        try:
            while not self.llm_result_queue.empty():
                agent_id, parsed_decision = self.llm_result_queue.get_nowait()
                agent = self.agent_manager.get_agent(agent_id)

                if agent and agent.is_alive():
                    log_agent_event(agent_id, f"Processing LLM response. Current plan: {agent.current_plan['plan'].name}", agent, level=logging.DEBUG)

                    if parsed_decision: # Check if LLM call succeeded & parsing in worker worked
                        try:
                            plan_name_str = parsed_decision.get("plan", "IDLE")
                            plan_target = parsed_decision.get("target") # Target can be None, int, list

                            # Check if this response was for a group decision
                            was_group_decision = agent.current_plan['plan'] == PlanType.RESPOND_TO_GROUP_REQUEST
                            # Convert plan name string to PlanType Enum
                            try:
                                parsed_plan_type = PlanType[plan_name_str.upper()]
                                log_agent_event(agent_id, f"LLM parsed plan type is '{parsed_plan_type}'. Group decision: {was_group_decision}")
                            except KeyError: 
                                log_agent_event(agent_id, f"LLM plan name '{plan_name_str}' unknown. Default IDLE.", agent, level=logging.WARNING); parsed_plan_type = PlanType.IDLE

                            # --- VVV Handle Specific Group Decision Responses VVV ---
                            if was_group_decision:
                                log_agent_event(agent_id, f"starting group logic.")
                                requester_id = plan_target if parsed_plan_type in [PlanType.ACCEPT_GROUP_FROM, PlanType.ATTACK_TARGET] else None
                                requester_agent = self.agent_manager.get_agent(requester_id) if requester_id is not None else None
                                log_agent_event(agent_id, f"Group decision was from requester '{requester_id}'. Parsed plan type is {parsed_plan_type}")

                                if parsed_plan_type == PlanType.ACCEPT_GROUP_FROM:
                                    log_agent_event(agent_id, f"LLM chose ACCEPT_GROUP_FROM {requester_id}.", agent)
                                    # --- Perform Acceptance Logic ---
                                    conditions_met = ( # Re-check conditions *now*
                                        agent.group_id is None and requester_agent and requester_agent.is_alive() and
                                        requester_agent.group_id is None and requester_id in agent.pending_group_requests_from and
                                        requester_agent.pending_group_request_to == agent.id and
                                        manhattan_distance((agent.x, agent.y), (requester_agent.x, requester_agent.y)) <= 1 )

                                    if conditions_met:
                                        new_group = self.group_manager.create_group_with_agents(agent.id, requester_id)
                                        if new_group:
                                            # Group formed successfully. Both agents' plans were reset by joining.
                                            # Also clear requester's waiting plan state
                                            requester_agent.set_new_plan({'plan': PlanType.IDLE, 'target': None, 'path': None})
                                        else: # Group creation failed unexpectedly
                                             new_plan = {'plan': PlanType.IDLE, 'target': None, 'path': None} # Keep self IDLE
                                             if requester_agent: requester_agent.set_new_plan({'plan': PlanType.IDLE, 'target': None, 'path': None}) # Reset requester too
                                    else: # Conditions no longer met
                                         log_agent_event(agent_id, f"Conditions to ACCEPT group from {requester_id} no longer met. Ignoring.", agent, level=logging.WARNING)
                                         new_plan = {'plan': PlanType.IDLE, 'target': None, 'path': None} # Keep self IDLE
                                         # Clear requester's state too
                                         if requester_agent:
                                              requester_agent.pending_group_request_to = None
                                              requester_agent.set_new_plan({'plan': PlanType.IDLE, 'target': None, 'path': None})
                                    # Clear the specific incoming request that was decided upon
                                    if requester_id in agent.pending_group_requests_from: agent.pending_group_requests_from.remove(requester_id)
                                    agent.set_new_plan(new_plan) # Set plan (likely IDLE if group formed/failed)
                                    continue # Skip normal plan processing below for this agent

                                elif parsed_plan_type == PlanType.ATTACK_AGENT:
                                     log_agent_event(agent_id, f"LLM chose ATTACK_AGENT {requester_id} instead of grouping.", agent)
                                     # Initiate combat if requester still valid
                                     if requester_agent and requester_agent.is_alive():
                                         self.combat_manager.initiate_combat(agent.id, requester_id)
                                     # Clear requester's outgoing request & waiting state
                                     if requester_agent:
                                         requester_agent.pending_group_request_to = None
                                         requester_agent.set_new_plan({'plan': PlanType.IDLE, 'target': None, 'path': None})
                                     # Clear the specific incoming request
                                     if requester_id in agent.pending_group_requests_from: agent.pending_group_requests_from.remove(requester_id)
                                     new_plan = {'plan': PlanType.IDLE, 'target': None, 'path': None} # Self becomes IDLE after deciding
                                     agent.set_new_plan(new_plan)
                                     continue # Skip normal processing

                                else: # Implicit rejection (IDLE or other plan chosen)
                                     log_agent_event(agent_id, f"LLM chose {parsed_plan_type.name} (Implicitly ignored group request from {requester_id}).", agent)
                                     # Clear requester's state
                                     if requester_agent:
                                         requester_agent.pending_group_request_to = None
                                         requester_agent.set_new_plan({'plan': PlanType.IDLE, 'target': None, 'path': None})
                                     # Clear the specific incoming request
                                     if requester_id in agent.pending_group_requests_from: agent.pending_group_requests_from.remove(requester_id)
                                     # Fall through to normal plan validation/setting below

                            # --- Normal Plan Validation/Setting ---
                            # (Only runs if NOT a specific group decision response handled above)
                            valid_target = None
                            if parsed_plan_type == PlanType.GO_TO_RESOURCE or parsed_plan_type == PlanType.GO_TO_POS:
                                if isinstance(plan_target, (list, tuple)) and len(plan_target) == 2 and \
                                   all(isinstance(n, (int, float)) for n in plan_target):
                                    # Target looks like valid coordinates
                                    valid_target = tuple(int(round(n)) for n in plan_target)
                                else:
                                     # Invalid target format for this plan type
                                     log_agent_event(agent_id, f"Invalid target format '{plan_target}' for plan {parsed_plan_type.name}. Defaulting to IDLE.", agent, level=logging.WARNING)
                                     parsed_plan_type = PlanType.IDLE # <<< ENSURE THIS LINE IS PRESENT
                                     valid_target = None
                            elif parsed_plan_type in [PlanType.GO_TO_AGENT, PlanType.FORM_GROUP_WITH, PlanType.ACCEPT_GROUP_FROM, PlanType.ATTACK_TARGET]:
                                if isinstance(plan_target, int):
                                    valid_target = plan_target # Target is an ID
                                else:
                                     # Invalid target format for this plan type
                                     log_agent_event(agent_id, f"Invalid target format '{plan_target}' for plan {parsed_plan_type.name}. Defaulting to IDLE.", agent, level=logging.WARNING)
                                     parsed_plan_type = PlanType.IDLE # <<< ENSURE THIS LINE IS PRESENT
                                     valid_target = None
                            elif parsed_plan_type == PlanType.EXPLORE:
                                 # Optional target, can be None or direction string
                                 valid_target = plan_target if isinstance(plan_target, str) and plan_target.upper() in ['N','S','E','W','NE','NW','SE','SW'] else None
                            elif parsed_plan_type == PlanType.IDLE:
                                 valid_target = None # IDLE needs no target

                            # --- Final check: If target ended up None for a plan that requires one ---
                            if valid_target is None and parsed_plan_type not in [PlanType.IDLE, PlanType.EXPLORE]:
                                 log_agent_event(agent_id, f"Plan {parsed_plan_type.name} requires a target but got None after validation. Defaulting IDLE.", agent, level=logging.WARNING)
                                 parsed_plan_type = PlanType.IDLE
                                 valid_target = None

                            new_plan = {'plan': parsed_plan_type, 'target': valid_target, 'path': None}
                            # Add pathfinding here if needed

                        except Exception as e:
                            log_agent_event(
                                agent_id,
                                f"Error parsing/validating LLM plan result. Type: {type(e).__name__}, Msg: {e}. Decision: {parsed_decision}",
                                agent,
                                level=logging.ERROR,
                                exc_info=True # Add traceback to log file for detailed debugging
                            )                            
                            new_plan = {'plan': PlanType.IDLE, 'target': None, 'path': None} # Default to IDLE on error

                            # Keep default IDLE plan
                    else: # LLM call failed
                         log_agent_event(agent_id, "LLM plan decision failed (worker returned None). Defaulting to IDLE.", agent, level=logging.WARNING)
                         new_plan = {'plan': PlanType.IDLE, 'target': None, 'path': None} # Default to IDLE on error
                         # Keep default IDLE plan

                    # Update agent's plan (unless handled specifically by ACCEPT/ATTACK above)
                    # The check 'was_group_decision' combined with 'continue' ensures we don't overwrite
                    # the state set during group acceptance/attack initiation.
                    if not was_group_decision or (parsed_plan_type != PlanType.ACCEPT_GROUP_FROM and parsed_plan_type != PlanType.ATTACK_AGENT):
                         agent.set_new_plan(new_plan)

                # ... (handle agent died while waiting) ...
                self.llm_result_queue.task_done() # Mark task processed

        except queue.Empty: pass # Normal
        except Exception as e: logging.error(f"Error processing LLM result queue: {e}", exc_info=True)

    def _render(self):
        """Draws the entire simulation state to the screen."""
        # --- Define surfaces ---
        grid_surface = self.screen.subsurface(pygame.Rect(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT - UI_AREA_HEIGHT))
        panel_surface = self.screen.subsurface(pygame.Rect(SCREEN_WIDTH, 0, INFO_PANEL_WIDTH, SCREEN_HEIGHT))
        # Make ui_surface cover the whole width for simplicity in coordinate handling now
        ui_surface = self.screen.subsurface(pygame.Rect(0, SCREEN_HEIGHT - UI_AREA_HEIGHT, TOTAL_WINDOW_WIDTH, UI_AREA_HEIGHT))

        # --- Clear/fill surfaces ---
        grid_surface.fill(COLOR_BLACK)
        panel_surface.fill(COLOR_PANEL_BG)
        ui_surface.fill(COLOR_BLACK) # Fill the whole bottom bar

        # --- Render Grid Surface ---
        # Draw Grid Lines
        for x in range(0, SCREEN_WIDTH, CELL_SIZE): pygame.draw.line(grid_surface, COLOR_GRID, (x, 0), (x, grid_surface.get_height()))
        for y in range(0, grid_surface.get_height(), CELL_SIZE): pygame.draw.line(grid_surface, COLOR_GRID, (0, y), (SCREEN_WIDTH, y))

        # --- VVV ADDED: Draw Axis Labels VVV ---
        label_interval = 1 # Draw label every 5 cells
        label_padding = 5  # Pixels padding from edge
        label_color = COLOR_TEXT

        # Y-axis labels (Left edge)
        for y_label in range(0, GRID_HEIGHT, label_interval):
            # Only draw if label fits within grid height bounds
            if (y_label * CELL_SIZE) < grid_surface.get_height():
                label_text = str(y_label)
                label_surf = self.font_small.render(label_text, True, label_color)
                # Position slightly indented from left, vertically aligned with grid line
                label_rect = label_surf.get_rect(topleft=(label_padding, y_label * CELL_SIZE + label_padding))
                grid_surface.blit(label_surf, label_rect)

        # X-axis labels (Top edge)
        for x_label in range(0, GRID_WIDTH, label_interval):
             # Only draw if label fits within grid width bounds
            if (x_label * CELL_SIZE) < grid_surface.get_width():
                label_text = str(x_label)
                label_surf = self.font_small.render(label_text, True, label_color)
                # Position slightly indented from top, horizontally aligned with grid line
                label_rect = label_surf.get_rect(topleft=(x_label * CELL_SIZE + label_padding, label_padding))
                grid_surface.blit(label_surf, label_rect)
        # --- ^^^ ADDED: Draw Axis Labels ^^^ ---

        # Draw Resources with Quantity Text
        for pos, res_info in self.resource_manager.resources.items():
             # Check if coordinates are valid just in case
             if not self.grid_manager.is_valid_coordinate(pos[0], pos[1]): continue

             rect = pygame.Rect(pos[0] * CELL_SIZE, pos[1] * CELL_SIZE, CELL_SIZE, CELL_SIZE)

             # Draw resource background based on quantity (same)
             quantity = res_info.get('quantity', 0)
             qty_ratio = min(1.0, quantity / RESOURCE_MAX_QUANTITY) if RESOURCE_MAX_QUANTITY > 0 else 1.0
             # Make color brighter for higher quantity
             intensity_color = tuple(int(c * (0.3 + qty_ratio * 0.7)) for c in COLOR_RESOURCE) # Brighter scale
             pygame.draw.rect(grid_surface, intensity_color, rect.inflate(-4,-4), border_radius=3)

             # --- VVV ADDED: Render Resource Quantity VVV ---
             quantity_text = f"{int(round(quantity))}" # Display integer quantity
             # Choose text color (e.g., black usually contrasts well with yellow)
             qty_text_color = COLOR_BLACK
             # Render using the small font
             qty_surf = self.font_small.render(quantity_text, True, qty_text_color)
             # Center the text within the resource rectangle
             qty_rect = qty_surf.get_rect(center=rect.center)
             # Blit the quantity text onto the grid surface, on top of the resource color
             grid_surface.blit(qty_surf, qty_rect)
             # --- ^^^ ADDED: Render Resource Quantity ^^^ ---

        # --- Agent Drawing ---
        agents_to_draw = self.agent_manager.get_all_agents() # Get current agents
        for agent in agents_to_draw:
            if not agent.is_alive(): continue # Skip dead agents

            rect = pygame.Rect(agent.x * CELL_SIZE, agent.y * CELL_SIZE, CELL_SIZE, CELL_SIZE)

            # Determine agent color (based on group or individual color)
            agent_draw_color = agent.color # Default to agent's own assigned color
            if agent.group_id is not None:
                group = self.group_manager.get_group(agent.group_id)
                if group:
                    agent_draw_color = group.color # Use group color if grouped
                else: # Fix inconsistent state if group disappeared
                    agent.group_id = None
                    # Keep agent's original color

            # Calculate center coordinates in pixels
            center_x = agent.x * CELL_SIZE + CELL_SIZE // 2
            center_y = agent.y * CELL_SIZE + CELL_SIZE // 2
            # Calculate radius in pixels (use the constant)
            radius_pixels = PERCEPTION_RADIUS * CELL_SIZE
            # Draw the circle outline (width=1)
            # Note: This circle might extend beyond the grid surface bounds, Pygame handles clipping.
            pygame.draw.circle(grid_surface, agent_draw_color, (center_x, center_y), radius_pixels, width=1)

            # Determine border based on state (selected, combat, pending interaction)
            border_color = COLOR_WHITE ; border_width = 1 # Default border
            is_selected = (agent.id == self.selected_agent_id)
            if agent.in_combat: border_color = COLOR_GROUP_END; border_width = 3
            elif agent.pending_group_request_to or agent.pending_group_requests_from: border_color = COLOR_RESOURCE; border_width = 2
            elif is_selected: border_color = COLOR_SELECTED_BORDER; border_width = 3

            # Draw agent body background and border
            pygame.draw.rect(grid_surface, agent_draw_color, rect.inflate(-2, -2), border_radius=5)
            pygame.draw.rect(grid_surface, border_color, rect.inflate(-2,-2), width=border_width, border_radius=5)

            # --- VVV ADDED: Render Agent ID VVV ---
            id_text = str(agent.id)
            # Choose a font (small font likely better) and color (e.g., black or white for contrast)
            # Determine text color based on background brightness for better visibility
            bg_lum = (0.299*agent_draw_color[0] + 0.587*agent_draw_color[1] + 0.114*agent_draw_color[2]) # Calculate luminance
            text_color = COLOR_BLACK if bg_lum > 128 else COLOR_WHITE # Use black on light, white on dark

            id_surf = self.font_small.render(id_text, True, text_color) # Use anti-aliasing (True)
            # Center the ID text within the agent's rectangle
            id_rect = id_surf.get_rect(center=rect.center)
            # Blit the ID text onto the grid surface
            grid_surface.blit(id_surf, id_rect)
            # --- ^^^ ADDED: Render Agent ID ^^^ ---

            # Draw HP bar (same logic as before)
            hp_ratio = agent.hp / agent.max_hp
            hp_bar_width = CELL_SIZE * 0.8; hp_bar_height = 4
            hp_bar_x = rect.left + (CELL_SIZE - hp_bar_width) / 2
            hp_bar_y = rect.top - hp_bar_height - 2
            hp_bar_y = max(0, hp_bar_y) # Clamp to screen top
            pygame.draw.rect(grid_surface, COLOR_BLACK, (hp_bar_x, hp_bar_y, hp_bar_width, hp_bar_height))
            hp_color = COLOR_GROUP_START if hp_ratio > 0.5 else (COLOR_RESOURCE if hp_ratio > 0.2 else COLOR_GROUP_END)
            pygame.draw.rect(grid_surface, hp_color, (hp_bar_x, hp_bar_y, hp_bar_width * hp_ratio, hp_bar_height))

            # --- VVV ADDED: Render Resource Bar VVV ---
            res_bar_height = 4 # Height of the resource bar
            res_bar_width = CELL_SIZE * 0.8 # Width similar to HP bar
            res_bar_x = rect.left + (CELL_SIZE - res_bar_width) / 2
            # Position resource bar at the BOTTOM INSIDE the agent rect
            res_bar_y = rect.bottom - res_bar_height - 3 # Position near bottom edge, inside padding

            # Calculate resource ratio (0.0 to 1.0)
            # Ensure AGENT_MAX_RESOURCES is not zero to avoid division error
            resource_ratio = 0.0
            if AGENT_MAX_RESOURCES > 0:
                 resource_ratio = max(0, min(1, agent.resource_level / AGENT_MAX_RESOURCES))

            # Draw the background for the resource bar (e.g., dark grey)
            res_bar_bg_color = (50, 50, 50)
            pygame.draw.rect(grid_surface, res_bar_bg_color, (res_bar_x, res_bar_y, res_bar_width, res_bar_height))
            # Draw the filled portion (use resource color - yellow)
            filled_width = res_bar_width * resource_ratio
            pygame.draw.rect(grid_surface, COLOR_RESOURCE, (res_bar_x, res_bar_y, filled_width, res_bar_height))
            # --- ^^^ ADDED: Render Resource Bar ^^^ ---

        # --- VVV ADDED: Draw Agent Trails VVV ---
        for agent in self.agent_manager.get_all_agents():
            if not agent.visited_trail: continue # Skip if trail empty

            trail_len = len(agent.visited_trail)
            # Iterate through trail points with index for gradient calculation
            for i, (pos, timestamp) in enumerate(agent.visited_trail):
                 # Check if pos is valid just in case trail has old invalid coords
                 if self.grid_manager.is_valid_coordinate(pos[0], pos[1]):
                      fade_factor = 1 - i / trail_len # Linear fade based on position in deque
                      # Interpolate between agent color and fade color
                      trail_color = interpolate_color(agent.color, TRAIL_FADE_COLOR, fade_factor)

                      # Draw a small rectangle or circle marker for the trail
                      trail_rect = pygame.Rect(pos[0] * CELL_SIZE, pos[1] * CELL_SIZE, CELL_SIZE, CELL_SIZE)
                      marker_size = CELL_SIZE // 4
                      marker_rect = pygame.Rect(0, 0, marker_size, marker_size)
                      marker_rect.center = trail_rect.center
                      pygame.draw.rect(grid_surface, trail_color, marker_rect, border_radius=2)

        # --- ^^^ ADDED: Draw Agent Trails ^^^ ---

        # --- Render Info Panel Surface ---
        if self.selected_agent_id is not None:
             agent = self.agent_manager.get_agent(self.selected_agent_id)
             if agent: self._draw_agent_info_panel(panel_surface, agent) # Call helper to draw details
             else: self.selected_agent_id = None # Agent died or invalid ID, deselect
        else: # Draw placeholder text if no agent is selected
            text_surf = self.font.render("Click agent to inspect", True, COLOR_TEXT)
            text_rect = text_surf.get_rect(center=(panel_surface.get_width() // 2, 30))
            panel_surface.blit(text_surf, text_rect)

        # --- Render UI Surface (Bottom Bar - Corrected Button Drawing) ---
        mouse_pos = pygame.mouse.get_pos()
        # Adjust mouse_pos Y to be relative to the ui_surface for hover/click checks *within* this area
        # Note: Button rect X is already relative to ui_surface's left edge (0)
        ui_mouse_pos_y = mouse_pos[1] - (SCREEN_HEIGHT - UI_AREA_HEIGHT)
        ui_mouse_pos = (mouse_pos[0], ui_mouse_pos_y) # Relative mouse pos for UI bar checks

        # Draw Pause/Play Button
        button = self.buttons['pause_play']
        color = COLOR_BUTTON
        # Use the relative mouse position and the button's relative rect for hover check
        if button['rect'].collidepoint(ui_mouse_pos):
            color = COLOR_BUTTON_HOVER

        # Draw the button rectangle using its relative coordinates directly onto ui_surface
        pygame.draw.rect(ui_surface, color, button['rect'], border_radius=5)

        # Render button text centered within the button's relative rect
        text_surf = self.font.render(button['text'], True, COLOR_BUTTON_TEXT)
        # text_rect's position is calculated relative to the button's rect (which is already relative to ui_surface)
        text_rect = text_surf.get_rect(center=button['rect'].center)
        # Blit the text onto the ui_surface using the calculated relative text_rect coordinates
        ui_surface.blit(text_surf, text_rect)

        # --- Draw Simulation Info Text ---
        info_y = UI_AREA_HEIGHT // 2 # Center text vertically
        texts = [
            f"Tick: {self.time_step}",
            f"Agents: {len(self.agent_manager.agents)}",
            f"Groups: {len(self.group_manager.groups)}",
            f"FPS: {self.clock.get_fps():.1f}"
        ]
        # Start text relative to the button drawn on the ui_surface
        x_offset = button['rect'].right + 30
        for text in texts:
            text_surface = self.font.render(text, True, COLOR_TEXT)
            # Blit onto ui_surface using coordinates relative to ui_surface
            text_rect = text_surface.get_rect(midleft=(x_offset, info_y))
            ui_surface.blit(text_surface, text_rect)
            x_offset += text_surface.get_width() + 30

        # --- Update the Full Display ---
        pygame.display.flip()


    def _draw_agent_info_panel(self, surface, agent):
        """Draws the selected agent's details and history onto the info panel surface."""
        y_pos = 10
        line_height = 22        # For standard font
        small_line_height = 18  # For log font
        panel_width = surface.get_width()
        padding = 10

        plan_str = f"Plan: {agent.current_plan['plan'].name}"
        if agent.current_plan['target'] is not None:
             plan_str += f" (Tgt: {agent.current_plan['target']})"
        if agent.is_waiting_for_llm: plan_str += " [WAITING LLM]"

        # --- Agent Status ---
        info_lines = [
            f"Agent ID: {agent.id}",
            f"Position: ({agent.x}, {agent.y})",
            f"HP: {agent.hp:.1f} / {agent.max_hp}",
            f"Resources: {agent.resource_level:.1f} / {AGENT_MAX_RESOURCES}",
            f"Group: {'None' if agent.group_id is None else f'Group {agent.group_id}'}",
            f"Combat: {'Yes' if agent.in_combat else 'No'}",
            f"  Target: {agent.in_combat_with_agent if agent.in_combat_with_agent is not None else agent.in_combat_with_group if agent.in_combat_with_group is not None else 'N/A'}",
            f"Pending Req To: {agent.pending_group_request_to if agent.pending_group_request_to is not None else 'None'}",
            f"Requests From: {', '.join(map(str, agent.pending_group_requests_from)) if agent.pending_group_requests_from else 'None'}",
            f"Plan: {plan_str})"
        ]
        for line in info_lines:
            text_surf = self.font.render(line, True, COLOR_TEXT)
            surface.blit(text_surf, (padding, y_pos))
            y_pos += line_height

        # --- History Log ---
        y_pos += 10 # Space before log
        # Divider Line
        pygame.draw.line(surface, COLOR_GRID, (padding, y_pos), (panel_width - padding, y_pos))
        y_pos += 5

        # Log Title
        title_surf = self.font.render("Recent History:", True, COLOR_WHITE)
        surface.blit(title_surf, (padding, y_pos))
        y_pos += line_height

        # Render Agent's Internal Log (most recent entries first)
        log_start_y = y_pos
        # Calculate how many lines fit in the remaining space
        max_log_lines_display = max(0, (surface.get_height() - log_start_y - padding) // small_line_height)
        num_entries_to_show = min(len(agent.history_log), max_log_lines_display)

        # Iterate backwards through the deque to show most recent first
        log_items_to_render = list(agent.history_log)[-num_entries_to_show:]

        for log_entry in log_items_to_render:
             # Render with smaller font
             log_surf = self.font_small.render(log_entry, True, COLOR_TEXT)
             # Truncate if too long? For now, let it wrap if Pygame supports it (it doesn't automatically)
             # Simple solution: Blit as is, might overflow panel width if very long.
             surface.blit(log_surf, (padding + 5, y_pos)) # Indent log entries
             y_pos += small_line_height