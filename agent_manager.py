from constants import *
from helper import *
from agent import Agent
import random

class AgentManager:
    def __init__(self, grid_manager, available_llm_configs): # Needs sim ref for queues
        self.agents = {}
        self.next_agent_id = 0
        self.grid_manager = grid_manager
        self.combat_manager = None # Set by Simulation
        self.available_llm_configs = available_llm_configs
        if not available_llm_configs:
            logging.warning("AgentManager initialized with no available LLM configs!")
        logging.info("AgentManager initialized.")

    def create_agent(self): # Removed model_name, timeout args
        """Creates a new agent and assigns an LLM config."""
        pos = self.grid_manager.get_random_empty_cell()
        if pos and self.available_llm_configs: # Check if configs are available
            agent_id = self.next_agent_id

            # Cycle through available configs based on agent ID
            config_index = agent_id % len(self.available_llm_configs)
            agent_config = self.available_llm_configs[config_index]
            logging.info(f"Assigning LLM config '{agent_config.get('id', 'N/A')}' to Agent {agent_id}")

            # Pass the selected config dictionary to the Agent constructor
            agent = Agent(agent_id, pos[0], pos[1], agent_config)

            self.agents[agent_id] = agent
            self.grid_manager.place_object(agent, pos[0], pos[1])
            self.next_agent_id += 1
            return agent
        elif not self.available_llm_configs:
            logging.error("Failed to create agent: No LLM configurations loaded.")
        else: # No empty cell
             logging.error("Failed to create agent: No empty cell found.")
        return None

    def get_agent(self, agent_id):
        """Retrieves an agent object by its ID."""
        return self.agents.get(agent_id)

    def get_all_agents(self):
        """Returns a list of all current agent objects."""
        return list(self.agents.values())

    def remove_agent(self, agent_id, group_manager):
        """Removes an agent from the simulation (e.g., upon death)."""
        agent = self.agents.pop(agent_id, None) # Remove from dict, get object if found
        if agent:
            log_agent_event(agent_id, "Removed from simulation (died/despawned).", agent_ref=None) # Can't use agent ref now
            self.grid_manager.remove_object(agent, agent.x, agent.y) # Remove from grid
            if agent.group_id:
                group_manager.remove_agent_from_group(agent.id, agent.group_id) # Notify group
            self.clear_pending_requests_involving(agent_id) # Clean up interactions
        return agent # Return removed agent or None

    def clear_pending_requests_involving(self, agent_id):
        """Clears pending group requests TO and FROM a specific agent (e.g., when they die)."""
        for other_agent in self.agents.values():
            # Clear requests initiated BY others TO the removed agent
            if agent_id in other_agent.pending_group_requests_from:
                log_agent_event(other_agent.id, f"Cleared incoming group request from removed Agent {agent_id}.", other_agent, level=logging.DEBUG)
                other_agent.pending_group_requests_from.remove(agent_id)
            # Clear requests initiated BY the removed agent TO others
            if other_agent.pending_group_request_to == agent_id:
                log_agent_event(other_agent.id, f"Cleared outgoing group request targetting removed Agent {agent_id}.", other_agent, level=logging.DEBUG)
                other_agent.pending_group_request_to = None


    def handle_deaths(self, group_manager):
        """Checks for dead agents and removes them."""
        # Find IDs of agents whose HP is 0 or less
        dead_agent_ids = [id for id, agent in self.agents.items() if not agent.is_alive()]
        for agent_id in dead_agent_ids:
            self.remove_agent(agent_id, group_manager) # Process removal

    # Modified to accept request_queue from Simulation._tick
    def initiate_llm_requests(self, group_manager, resource_manager, current_time_step, request_queue):
        """Checks agents and queues requests (agent_id, context) for those needing plans."""
        self.update_pending_requests() # Update request statuses first
        for agent_id, agent in list(self.agents.items()):
             if agent.is_alive() and not agent.in_combat and agent.llm_config: # Check if agent has config
                 agent.simulation_time_step = current_time_step

                 llm_request_queued = False # Flag to prevent queuing multiple requests per tick

                 if agent.group_request_pending_decision and not agent.is_waiting_for_llm:
                     # Immediately queue a focused group decision request
                     context = agent.get_state_for_llm(self.grid_manager, self, group_manager, resource_manager) # Method now generates focused prompt
                     request_data = (agent.id, context)
                     request_queue.put(request_data)

                     # Update agent state
                     agent.is_waiting_for_llm = True
                     agent.group_request_pending_decision = False # Decision is now pending LLM response
                     agent.current_plan = {'plan': PlanType.RESPOND_TO_GROUP_REQUEST, 'target': None, 'path': None} # Set specific plan state

                     log_agent_event(agent.id, "Queued LLM group decision request.", agent, level=logging.INFO)
                     llm_request_queued = True # Mark that a request was queued

                 if agent.current_plan['plan'] == PlanType.EXPLORE:
                    # Check current perception (memory updated at start of tick)
                    # What constitutes "something significant"? Another agent or a known resource nearby.
                    nearby_significant = False
                    # Use known_resources which is updated by perception
                    if agent.known_resources:
                        # Check if any *known* resource is now adjacent or very close
                        for pos, info in agent.known_resources.items():
                            if info.get('last_seen_quantity', 0) > 0 and manhattan_distance((agent.x, agent.y), pos) <= 1: # Found adjacent known resource
                                    nearby_significant = True
                                    log_agent_event(agent_id, f"Interrupted EXPLORE: Now adjacent to known resource at {pos}.", agent)
                                    break
                    # Also check current perception grid for *any* nearby agents (re-use perception logic concept)
                    if not nearby_significant:
                        perception_check_radius = PERCEPTION_RADIUS # Or maybe a smaller radius like 2 for interruption? Let's use constant for now.
                        visible_objects_now = self.grid_manager.get_objects_in_radius(agent.x, agent.y, perception_check_radius)
                        for obj, pos in visible_objects_now:
                            if obj != agent and isinstance(obj, Agent): # Found another agent nearby
                                nearby_significant = True
                                log_agent_event(agent_id, f"Interrupted EXPLORE: Detected nearby Agent {obj.id} at {pos}.", agent)
                                break

                    if nearby_significant:
                        # Interrupt explore: set plan to IDLE to force LLM re-evaluation next tick
                        agent.set_new_plan({'plan': PlanType.IDLE, 'target': None, 'path': None})
                        # No need to queue LLM request here, the IDLE state will trigger it below if frequency allows


                 # --- Normal Plan Request Logic (only if group decision wasn't queued) ---
                 if not llm_request_queued:

                     needs_new_plan = (agent.current_plan['plan'] == PlanType.IDLE)
                     time_to_decide = (agent.ticks_since_last_llm_decision >= LLM_DECISION_FREQUENCY)
                     can_request = not agent.is_waiting_for_llm

                     if needs_new_plan and time_to_decide and can_request:
                         agent.ticks_since_last_llm_decision = 0 # Reset counter
                         context = agent.get_state_for_llm(self.grid_manager, self, group_manager, resource_manager) # Normal prompt
                         request_data = (agent.id, context)
                         request_queue.put(request_data)
                         agent.is_waiting_for_llm = True
                         # Optionally set plan to WAITING_LLM? or keep IDLE? Keep IDLE is simpler.
                         log_agent_event(agent.id, "Queued LLM plan request (Idle trigger).", agent, level=logging.DEBUG)
                         llm_request_queued = True
                     else:
                         # Increment counter if no request was queued this tick for this agent
                         agent.ticks_since_last_llm_decision += 1



    def request_decisions(self, group_manager, resource_manager, current_time_step):
        """Requests decisions from all living agents."""
        self.update_pending_requests() # Update request statuses first
        agent_actions = {}
        # Iterate over a copy of keys in case agents are removed during iteration (unlikely here)
        for agent_id in list(self.agents.keys()):
             agent = self.get_agent(agent_id) # Get current agent object
             if agent and agent.is_alive():
                 agent.simulation_time_step = current_time_step # Update agent's time context
                 action = agent.request_decision(self.grid_manager, self, group_manager, resource_manager)
                 agent_actions[agent_id] = action
        return agent_actions

    def update_pending_requests(self):
        """Checks validity of pending group requests (e.g., distance, agent status)."""
        # Iterate over copy of items in case agents are removed
        for agent_id, agent in list(self.agents.items()):
            if not agent.is_alive(): continue # Skip dead agents

            # Check request initiated BY this agent
            target_id = agent.pending_group_request_to
            if target_id is not None:
                 target_agent = self.get_agent(target_id)
                 # Check if target is invalid or moved too far away
                 if not target_agent or not target_agent.is_alive() or target_agent.group_id is not None or \
                    manhattan_distance((agent.x, agent.y), (target_agent.x, target_agent.y)) > 2: # Allow distance 1 or 2? Let's use 2.
                      log_agent_event(agent.id, f"Outgoing group request to {target_id} expired (target invalid/moved/grouped).", agent, level=logging.DEBUG)
                      agent.pending_group_request_to = None
                      # Also remove from target's incoming queue if it exists there
                      if target_agent and agent.id in target_agent.pending_group_requests_from:
                          target_agent.pending_group_requests_from.remove(agent.id)

            # Check requests made TO this agent
            requests_from = list(agent.pending_group_requests_from) # Iterate copy
            for requester_id in requests_from:
                requester_agent = self.get_agent(requester_id)
                # Check if requester is invalid or moved too far away
                if not requester_agent or not requester_agent.is_alive() or requester_agent.group_id is not None or \
                   manhattan_distance((agent.x, agent.y), (requester_agent.x, requester_agent.y)) > 2:
                       log_agent_event(agent.id, f"Incoming group request from {requester_id} expired (requester invalid/moved/grouped).", agent, level=logging.DEBUG)
                       agent.pending_group_requests_from.remove(requester_id)
                       # Also clear the requester's outgoing request if it matches this agent
                       if requester_agent and requester_agent.pending_group_request_to == agent.id:
                           requester_agent.pending_group_request_to = None

    def execute_agent_plan_step(self, agent_id, group_manager, resource_manager):
        """Executes ONE step towards the agent's current plan."""
        agent = self.get_agent(agent_id)
        # Allow execution even if waiting for LLM? Yes, agent continues previous plan/action conceptually.
        # But skip if dead or in combat (combat manager handles combat turns)
        if not agent or not agent.is_alive() or agent.in_combat: return

        plan_info = agent.current_plan
        plan_type = plan_info['plan']
        target = plan_info.get('target')

        # --- VVV Handle Waiting States VVV ---
        # If waiting for any LLM response or group response, maybe just idle?
        # Or let previous plan execution continue? Let's make it idle for clarity.
        if plan_type == PlanType.WAITING_LLM or plan_type == PlanType.WAITING_GROUP_RESPONSE or plan_type == PlanType.RESPOND_TO_GROUP_REQUEST:
             log_agent_event(agent_id, f"Executing IDLE step while in plan state {plan_type.name}", agent, level=logging.DEBUG)
             # Do nothing actively, wait for _process_llm_results to change plan
             return
        # --- ^^^ Handle Waiting States ^^^ ---

        log_agent_event(agent_id, f"Executing step for Plan: {plan_type.name}, Target: {target}", agent, level=logging.DEBUG)

        # --- Execute Step Based on Plan ---
        if plan_type == PlanType.IDLE:
            # Do nothing, LLM request will be triggered if needed by initiate_llm_requests
            pass

        elif plan_type == PlanType.EXPLORE:
            current_pos = (agent.x, agent.y)
            possible_next_steps = []
            valid_directions = ['N','S','E','W','NE','NW','SE','SW'] # All adjacent directions

            # Find walkable adjacent cells
            walkable_adjacent = {} # direction -> pos
            for direction in valid_directions:
                dx, dy = 0, 0
                move_map = {'N': (0, -1), 'S': (0, 1), 'E': (1, 0), 'W': (-1, 0),
                            'NE': (1, -1), 'NW': (-1, -1), 'SE': (1, 1), 'SW': (-1, 1)}
                if direction in move_map: dx, dy = move_map[direction]
                next_x, next_y = current_pos[0] + dx, current_pos[1] + dy

                if self.grid_manager.is_valid_coordinate(next_x, next_y):
                    # Check if blocked by another agent
                    target_objects = self.grid_manager.get_objects_at(next_x, next_y)
                    is_blocked = any(isinstance(obj, Agent) for obj in target_objects)
                    if not is_blocked:
                        walkable_adjacent[direction] = (next_x, next_y)

            if not walkable_adjacent:
                # Boxed in! Cannot explore further. Revert to IDLE.
                log_agent_event(agent_id, "Cannot EXPLORE, no walkable adjacent cells. Setting plan to IDLE.", agent, level=logging.WARNING)
                agent.set_new_plan({'plan': PlanType.IDLE, 'target': None, 'path': None})
                return # Stop execution for this agent this tick

            # Prioritize unvisited squares
            visited_pos_set = set(pos for pos, ts in agent.visited_trail) # Get set of recently visited positions
            unvisited_walkable = {}
            visited_walkable = {}

            for direction, pos in walkable_adjacent.items():
                if pos not in visited_pos_set:
                    unvisited_walkable[direction] = pos
                else:
                    visited_walkable[direction] = pos

            chosen_direction = None
            if unvisited_walkable:
                # Prefer unvisited squares
                chosen_direction = random.choice(list(unvisited_walkable.keys()))
                log_agent_event(agent_id, f"Exploring towards unvisited {chosen_direction} to {unvisited_walkable[chosen_direction]}.", agent, level=logging.DEBUG)
            elif visited_walkable:
                # If all walkable are visited, pick a random visited one to avoid getting stuck
                chosen_direction = random.choice(list(visited_walkable.keys()))
                log_agent_event(agent_id, f"Exploring towards visited {chosen_direction} to {visited_walkable[chosen_direction]} (all adjacent visited).", agent, level=logging.DEBUG)
            # else: Should have been caught by 'not walkable_adjacent' check

            if chosen_direction:
                self._execute_move(agent, chosen_direction, agent.simulation_time_step) # Pass time step
            else: # Should not happen if walkable_adjacent check passed
                 log_agent_event(agent_id, "Error determining explore direction despite walkable cells. Setting IDLE.", agent, level=logging.ERROR)
                 agent.set_new_plan({'plan': PlanType.IDLE, 'target': None, 'path': None})


        elif plan_type in [PlanType.GO_TO_POS, PlanType.GO_TO_RESOURCE, PlanType.GO_TO_AGENT]:
            if target is None: # Invalid plan target
                log_agent_event(agent_id, f"Plan {plan_type.name} has no target. Setting to IDLE.", agent, level=logging.WARNING)
                agent.set_new_plan({'plan': PlanType.IDLE, 'target': None, 'path': None})
                return

            target_pos = None
            if plan_type == PlanType.GO_TO_AGENT:
                target_agent = self.get_agent(target)
                if target_agent and target_agent.is_alive(): target_pos = (target_agent.x, target_agent.y)
                else: # Target agent gone/dead, invalidate plan
                     log_agent_event(agent_id, f"Target Agent {target} for {plan_type.name} invalid. Setting plan to IDLE.", agent)
                     agent.set_new_plan({'plan': PlanType.IDLE, 'target': None, 'path': None}); return
            else: # GO_TO_POS or GO_TO_RESOURCE, target is coords
                if isinstance(target, (list, tuple)) and len(target) == 2: target_pos = tuple(target)
                else: # Invalid coordinate target
                    log_agent_event(agent_id, f"Target {target} for {plan_type.name} invalid coords. Setting plan to IDLE.", agent, level=logging.WARNING)
                    agent.set_new_plan({'plan': PlanType.IDLE, 'target': None, 'path': None}); return

            ####
            current_pos = (agent.x, agent.y)
            if current_pos == target_pos:
                # --- Agent Has Arrived at Target ---
                log_agent_event(agent_id, f"Arrived at target {target_pos} for plan {plan_type.name}.", agent, level=logging.DEBUG)

                if plan_type == PlanType.GO_TO_RESOURCE:
                    # --- VVV Start/Continue Timed Harvesting Logic VVV ---
                    # Check if resource still exists and agent isn't full
                    resource_info = resource_manager.resources.get(current_pos) # Use .get for safety
                    agent_full = agent.resource_level >= AGENT_MAX_RESOURCES

                    if resource_info and resource_info['quantity'] > 0 and not agent_full:
                        # Calculate amount to harvest this tick
                        amount_this_tick = min(agent.harvest_rate, resource_info['quantity'], AGENT_MAX_RESOURCES - agent.resource_level)

                        if amount_this_tick > 0:
                            # Perform the harvest action for this tick
                            harvest_success = resource_manager.harvest_resource_at(agent, current_pos, amount_this_tick) # Logs success internally

                            if harvest_success:
                                # Get the *new* quantity remaining after harvest
                                updated_res_info = resource_manager.resources.get(current_pos)
                                new_quantity = updated_res_info['quantity'] if updated_res_info else 0
                                # Update the agent's own memory immediately
                                agent.known_resources[current_pos] = {
                                    'type': resource_info.get('type', 'Unknown'), # Use original type pre-depletion
                                    'last_seen_quantity': new_quantity,
                                    'last_seen_tick': agent.simulation_time_step # Use current tick
                                }
                                log_agent_event(agent_id, f"Updated known quantity at {current_pos} to {new_quantity:.1f} after harvesting.", agent, level=logging.DEBUG)
                                # Optional: Update group memory here too? More complex. Let perception handle group sync for now.
                                # if agent.group_id is not None:
                                #    group = group_manager.get_group(agent.group_id)
                                #    if group and current_pos in group.group_known_resources:
                                #         group.group_known_resources[current_pos]['last_seen_quantity'] = new_quantity
                                #         group.group_known_resources[current_pos]['last_seen_tick'] = agent.simulation_time_step

                            # Check completion conditions *after* harvesting
                            resource_still_present = current_pos in resource_manager.resources # Re-check as harvest might deplete it
                            resource_has_quantity = resource_still_present and resource_manager.resources[current_pos]['quantity'] > 0
                            agent_full_now = agent.resource_level >= AGENT_MAX_RESOURCES

                            if not resource_has_quantity or agent_full_now:
                                # Harvesting complete (depleted OR agent full)
                                log_agent_event(agent_id, f"Finished HARVESTING at {target_pos} ({'depleted' if not resource_has_quantity else 'agent full'}). Setting plan to IDLE.", agent)
                                agent.set_new_plan({'plan': PlanType.IDLE, 'target': None, 'path': None})
                            # else: Resource remains & agent not full -> Plan remains GO_TO_RESOURCE, will harvest again next tick

                        else: # Cannot harvest (agent full? resource 0?) - Should be caught by initial check, but safety else
                             log_agent_event(agent_id, f"Cannot harvest at {target_pos} (agent full or resource empty?). Setting plan to IDLE.", agent, level=logging.WARNING)
                             agent.set_new_plan({'plan': PlanType.IDLE, 'target': None, 'path': None})

                    else: # Resource gone before harvesting started OR agent arrived full
                        log_agent_event(agent_id, f"Cannot start HARVEST at {target_pos} ({'resource gone' if not resource_info else 'agent full'}). Setting plan to IDLE.", agent, level=logging.WARNING)
                        agent.set_new_plan({'plan': PlanType.IDLE, 'target': None, 'path': None})
                    # --- ^^^ End Timed Harvesting Logic ^^^ ---

                elif plan_type == PlanType.GO_TO_AGENT:
                    # Arrived at agent, now initiate interaction based on original goal
                    # This part is tricky - why did we go to the agent? Assume grouping for now.
                    # TODO: Need LLM plan to be more specific, e.g., "GoToAndGroup" vs "GoToAndAttack"
                    log_agent_event(agent_id, f"Arrived at Agent {target}. Initiating FORM_GROUP request.", agent)
                    target_agent = self.get_agent(target) # Re-get target agent
                    if agent.group_id is None and target_agent and target_agent.is_alive() and target_agent.group_id is None:
                         agent.pending_group_request_to = target
                         target_agent.pending_group_requests_from.add(agent.id)
                         log_agent_event(agent_id, f"Initiated GROUP REQUEST to Agent {target}.", agent)
                         log_agent_event(target, f"Received GROUP REQUEST from Agent {agent_id}.", target_agent)
                    # Set plan to IDLE, wait for acceptance or new plan
                    agent.set_new_plan({'plan': PlanType.IDLE, 'target': None, 'path': None})

                else: # GO_TO_POS completed
                     agent.set_new_plan({'plan': PlanType.IDLE, 'target': None, 'path': None})

            else: # Not at target, execute one move step
                # --- VVV REVISED Movement Logic VVV ---
                dx = target_pos[0] - current_pos[0]
                dy = target_pos[1] - current_pos[1]

                v_dir = "" # Vertical component (N or S)
                h_dir = "" # Horizontal component (E or W)

                # Determine vertical direction
                if dy > 0: v_dir = "S"
                elif dy < 0: v_dir = "N"

                # Determine horizontal direction
                if dx > 0: h_dir = "E"
                elif dx < 0: h_dir = "W"

                # Combine components: Vertical first, then Horizontal
                # This guarantees "", N, S, E, W, NE, NW, SE, SW
                move_dir = v_dir + h_dir
                # --- ^^^ REVISED Movement Logic ^^^ ---

                if move_dir: # Ensure a direction was determined (dx or dy was non-zero)
                    move_successful = self._execute_move(agent, move_dir, agent.simulation_time_step) # <<< Pass time step
                    if not move_successful:
                        # Move failed, reset plan
                        log_agent_event(agent_id, f"Move {move_dir} failed/blocked. Resetting plan to IDLE.", agent, level=logging.INFO)
                        agent.set_new_plan({'plan': PlanType.IDLE, 'target': None, 'path': None})
                else:
                    # This case should ideally only happen if current_pos == target_pos,
                    # which is handled by the 'if' block above. Log warning if reached.
                    log_agent_event(agent_id, f"Move calculation resulted in empty direction (dx={dx}, dy={dy}). Already at target?", agent, level=logging.WARNING)
                    # Set plan to IDLE to force re-evaluation if stuck
                    agent.set_new_plan({'plan': PlanType.IDLE, 'target': None, 'path': None})

        elif plan_type == PlanType.FORM_GROUP_WITH:
             target_agent_id = target
             target_agent = self.get_agent(target_agent_id)
             conditions_met = ( # Recalculate conditions at time of execution
                 agent.group_id is None and
                 target_agent and target_agent.is_alive() and target_agent.group_id is None and
                 target_agent_id != agent.id and
                 manhattan_distance((agent.x, agent.y), (target_agent.x, target_agent.y)) <= 1
             )
             logging.debug(f"Agent {agent_id} executing FORM_GROUP conditions check: Met={conditions_met}")

             if conditions_met:
                 # Initiate request & state changes
                 agent.pending_group_request_to = target_agent_id
                 target_agent.pending_group_requests_from.add(agent.id)
                 # --- VVV Set Target Flag & Self Plan VVV ---
                 target_agent.group_request_pending_decision = True # Signal target to decide
                 agent.set_new_plan({'plan': PlanType.WAITING_GROUP_RESPONSE, 'target': target_agent_id, 'path': None}) # Set self to wait
                 # --- ^^^ Set Target Flag & Self Plan ^^^ ---
                 log_agent_event(agent_id, f"Initiated GROUP REQUEST to Agent {target_agent_id}. Now WAITING_GROUP_RESPONSE.", agent)
                 log_agent_event(target_agent_id, f"Received GROUP REQUEST from Agent {agent_id}. Decision pending.", target_agent)
             else:
                 # Failed to initiate
                 log_agent_event(agent_id, f"Execute FORM_GROUP_WITH {target_agent_id} failed (conditions changed?). Setting plan to IDLE.", agent, level=logging.WARNING)
                 agent.set_new_plan({'plan': PlanType.IDLE, 'target': None, 'path': None})

        elif plan_type == PlanType.ACCEPT_GROUP_FROM:
             # This plan is chosen by LLM, but execution happens in _process_llm_results immediately
             # This block shouldn't normally be reached if _process_llm_results handles it.
             # If reached, implies LLM response processing failed somehow? Set to IDLE.
             log_agent_event(agent_id, f"Reached execute step for ACCEPT_GROUP_FROM (should be handled in results). Setting to IDLE.", agent, level=logging.WARNING)
             agent.set_new_plan({'plan': PlanType.IDLE, 'target': None, 'path': None})


        elif plan_type == PlanType.ATTACK_TARGET:
             # This plan is chosen by LLM, but execution happens in _process_llm_results immediately
             # Or should it? Maybe check range here and initiate? Let's keep initiation here.
             target_id = target
             if target_id is not None:
                  success = False
                  p1, type1, p2, type2 = self.combat_manager.get_combat_participants(agent.id, target_id)
                  if p1 and p2:
                      initiator_id_for_combat = agent.group_id if agent.group_id is not None else agent.id
                      # Check distance before initiating?
                      dist = 999
                      if type2 == 'agent': dist = manhattan_distance((agent.x, agent.y), (p2.x, p2.y))
                      elif type2 == 'group': # Check distance to group centroid or closest member? Simple: centroid
                           centroid = p2.get_centroid()
                           if centroid: dist = manhattan_distance((agent.x, agent.y), centroid)

                      if dist <= 1: # Only attack adjacent for now
                          success = self.combat_manager.initiate_combat(initiator_id_for_combat, target_id)
                      else:
                          log_agent_event(agent_id, f"Plan ATTACK_TARGET {target_id} failed (target not adjacent).", agent, level=logging.INFO)

                  if not success: # If initiation failed (out of range, invalid, etc.)
                      agent.set_new_plan({'plan': PlanType.IDLE, 'target': None, 'path': None})
                  # If success, agent state becomes in_combat, stopping plan execution next tick

             else: # Invalid target
                  agent.set_new_plan({'plan': PlanType.IDLE, 'target': None, 'path': None})

    def _execute_move(self, agent, direction, current_time_step):
         """Helper to execute a move action."""
         dx, dy = 0, 0
         move_map = {'N': (0, -1), 'S': (0, 1), 'E': (1, 0), 'W': (-1, 0),
                     'NE': (1, -1), 'NW': (-1, -1), 'SE': (1, 1), 'SW': (-1, 1)}
         if direction in move_map:
              dx, dy = move_map[direction]
              new_x, new_y = agent.x + dx, agent.y + dy

            # --- VVV ADDED COLLISION CHECK VVV ---
              if self.grid_manager.is_valid_coordinate(new_x, new_y):
                  objects_in_target = self.grid_manager.get_objects_at(new_x, new_y)
                  # Check if any object in the target cell is another Agent
                  is_occupied_by_agent = any(isinstance(obj, Agent) for obj in objects_in_target)

                  if is_occupied_by_agent:
                      # Target cell is occupied by another agent, block move
                      log_agent_event(agent.id, f"Cannot move {direction} to ({new_x},{new_y}), cell occupied by another agent.", agent, level=logging.DEBUG)
                      return False # Indicate move failed
                  else:
                      # Target cell is valid and not occupied by an agent, proceed with move
                      # grid_manager.move_object handles removing from old, placing in new, and updating agent coords
                      move_success = self.grid_manager.move_object(agent, agent.x, agent.y, new_x, new_y)
                      if move_success:
                          agent.visited_trail.append(((new_x, new_y), current_time_step))
                          log_agent_event(agent.id, f"Added ({new_x},{new_y}) to visited trail.", agent, level=logging.DEBUG)
              else:
                  # Target coordinate itself is invalid
                  log_agent_event(agent.id, f"Cannot move {direction} to ({new_x},{new_y}), invalid coordinate.", agent, level=logging.WARNING)
                  return False # Indicate move failed
              # --- ^^^ END COLLISION CHECK ^^^ ---
         else:
              log_agent_event(agent.id, f"Tried invalid internal MOVE direction: {direction}", agent, level=logging.ERROR)

     # (execute_agent_action - unchanged, operates on agent.current_action)
    def execute_agent_action(self, agent_id, group_manager, resource_manager, combat_manager):
        agent = self.get_agent(agent_id)
        if not agent or not agent.is_alive() or agent.in_combat: return

        action = agent.current_action # Use the agent's current stored action
        action_type = action['action']
        target = action['target']
        # Log execution attempt (maybe DEBUG level)
        log_agent_event(agent_id, f"Attempting Execute: {action_type.name}, Target: {target}", agent, level=logging.DEBUG)

        # --- Execute Actions ---
        if action_type == ActionType.MOVE:
            dx, dy = 0, 0
            move_map = {'N': (0, -1), 'S': (0, 1), 'E': (1, 0), 'W': (-1, 0),
                        'NE': (1, -1), 'NW': (-1, -1), 'SE': (1, 1), 'SW': (-1, 1)}
            if target in move_map:
                 dx, dy = move_map[target]
                 new_x, new_y = agent.x + dx, agent.y + dy
                 # move_object handles grid updates and agent internal coord update
                 if not self.grid_manager.move_object(agent, agent.x, agent.y, new_x, new_y):
                      log_agent_event(agent_id, f"Failed to move {target} to ({new_x},{new_y}) (blocked/invalid?).", agent, level=logging.WARNING)
            else:
                log_agent_event(agent_id, f"Tried invalid MOVE target: {target}", agent, level=logging.WARNING)

        elif action_type == ActionType.COLLECT_RESOURCE:
             if isinstance(target, tuple) and len(target) == 2:
                  if target == (agent.x, agent.y): # Agent must be on the target cell
                     collected = resource_manager.collect_resource(agent, agent.x, agent.y)
                     if collected: agent.current_action = {'action': ActionType.IDLE, 'target': None} # Reset action after success
                     # ResourceManager logs success/failure details
                  else: # Agent is not on the target cell
                       log_agent_event(agent.id, f"Wants to collect at {target} but is at ({agent.x}, {agent.y}). Needs to move.", agent, level=logging.DEBUG)
             else: # Target format was invalid
                 log_agent_event(agent.id, f"Tried COLLECT with invalid target format: {target}", agent, level=logging.WARNING)

        elif action_type == ActionType.FORM_GROUP:
             target_agent_id = target
             target_agent = self.get_agent(target_agent_id)
             # Check conditions: self is ungrouped, target exists, alive, ungrouped, not self, nearby
             if (agent.group_id is None and target_agent and target_agent.is_alive() and
                 target_agent.group_id is None and target_agent_id != agent.id and
                 manhattan_distance((agent.x, agent.y), (target_agent.x, target_agent.y)) <= 1): # Adjacent check

                 # Initiate the request state
                 agent.pending_group_request_to = target_agent_id
                 target_agent.pending_group_requests_from.add(agent.id)
                 log_agent_event(agent_id, f"Initiated GROUP REQUEST to Agent {target_agent_id}.", agent)
                 log_agent_event(target_agent_id, f"Received GROUP REQUEST from Agent {agent_id}.", target_agent)
             else: # Log why initiation failed
                  fail_reason = "target invalid/grouped/too far/self"
                  if not target_agent: fail_reason = "target does not exist"
                  elif not target_agent.is_alive(): fail_reason = "target is dead"
                  elif target_agent.group_id is not None: fail_reason = "target already grouped"
                  elif manhattan_distance((agent.x, agent.y), (target_agent.x, target_agent.y)) > 1: fail_reason = "target not adjacent"
                  log_agent_event(agent_id, f"Failed to initiate FORM_GROUP request to {target_agent_id} ({fail_reason}).", agent, level=logging.WARNING)

        elif action_type == ActionType.ACCEPT_GROUP:
             requester_id = target
             requester_agent = self.get_agent(requester_id)
             # Check validity: self is ungrouped, requester exists, alive, ungrouped, requested self, adjacent
             if (agent.group_id is None and requester_agent and requester_agent.is_alive() and
                 requester_agent.group_id is None and
                 requester_agent.pending_group_request_to == agent.id and # Requester must have requested agent
                 requester_id in agent.pending_group_requests_from and # Agent must have received request
                 manhattan_distance((agent.x, agent.y), (requester_agent.x, requester_agent.y)) <= 1): # Adjacent check

                 # Conditions met - Form the group! GroupManager handles logging/state changes.
                 group_manager.create_group_with_agents(agent.id, requester_id)
             else: # Log why acceptance failed
                 fail_reason = "conditions not met/request expired"
                 if not requester_agent or not requester_agent.is_alive() or requester_agent.group_id is not None: fail_reason = "requester invalid/grouped"
                 elif requester_agent.pending_group_request_to != agent.id: fail_reason = "requester did not request self"
                 elif requester_id not in agent.pending_group_requests_from: fail_reason = "did not receive request from requester"
                 elif manhattan_distance((agent.x, agent.y), (requester_agent.x, requester_agent.y)) > 1: fail_reason = "requester not adjacent"
                 log_agent_event(agent.id, f"Failed to ACCEPT_GROUP from {requester_id} ({fail_reason}).", agent, level=logging.WARNING)

             # Clear the specific incoming request from this agent, whether successful or not
             if requester_id in agent.pending_group_requests_from:
                  agent.pending_group_requests_from.remove(requester_id)

        elif action_type == ActionType.ATTACK_GROUP:
            target_group_id = target
            if agent.group_id is not None: # Agent must be in a group to attack another group
                # CombatManager handles validation and logging
                combat_manager.initiate_combat(agent.group_id, target_group_id)
            else:
                log_agent_event(agent_id, f"Cannot execute ATTACK_GROUP (ungrouped).", agent, level=logging.WARNING)

        elif action_type == ActionType.ATTACK_AGENT:
             target_agent_id = target
             target_agent = self.get_agent(target_agent_id)
             # Check target validity
             if target_agent and target_agent.is_alive() and target_agent_id != agent.id:
                  # CombatManager handles validation (e.g., not same group) and logging
                  combat_manager.initiate_combat(agent.id, target_agent_id)
             else:
                  log_agent_event(agent_id, f"Failed ATTACK_AGENT {target_agent_id} (target invalid/dead/self).", agent, level=logging.WARNING)

        elif action_type == ActionType.IDLE:
             log_agent_event(agent_id, f"Executing IDLE.", agent, level=logging.DEBUG) # Log idle action if debugging needed

    def apply_consumption(self):
        """Applies resource consumption and starvation damage to all agents."""
        for agent in self.agents.values():
             if agent.is_alive():
                 agent.consume_resource() # Agent method handles logic and logging

    def update_agent_perception_and_memory(self, current_time_step):
        """Updates each agent's known_resources based on current perception."""
        # Optional: Memory decay threshold (e.g., forget resources not seen for 100 ticks)
        FORGET_THRESHOLD = 100

        for agent_id, agent in list(self.agents.items()):
            if not agent.is_alive(): continue

            visible_radius = 5 # Define perception range
            visible_objects = self.grid_manager.get_objects_in_radius(agent.x, agent.y, visible_radius)
            seen_resource_positions_this_tick = set()

            for obj, pos in visible_objects:
                if isinstance(obj, tuple) and obj[0] == 'Resource':
                    res_info = obj[1]
                    # Update agent's memory with current info
                    agent.known_resources[pos] = {
                        'type': res_info.get('type', 'Unknown'),
                        'last_seen_quantity': res_info.get('quantity', 0),
                        'last_seen_tick': current_time_step
                    }
                    seen_resource_positions_this_tick.add(pos)
                    # If agent is grouped, update group memory too? Maybe redundant if agent prompt uses group memory.
                    # Let's skip direct group memory update here for now.

            # Optional: Memory Decay - Remove old entries not seen this tick
            if FORGET_THRESHOLD is not None:
                known_positions = list(agent.known_resources.keys()) # Iterate over copy
                for pos in known_positions:
                    if pos not in seen_resource_positions_this_tick: # Only check decay if not currently visible
                         last_seen = agent.known_resources[pos].get('last_seen_tick', -1)
                         if current_time_step - last_seen > FORGET_THRESHOLD:
                              del agent.known_resources[pos]
                              log_agent_event(agent_id, f"Forgot resource location {pos} (not seen for {FORGET_THRESHOLD}+ ticks).", agent, level=logging.DEBUG)

            # If agent is grouped, potentially sync its knowledge with group knowledge?
            # Or assume prompt uses group knowledge. Let's assume prompt uses group knowledge.
            # No sync needed here if prompt accesses group.group_known_resources. 