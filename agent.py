from constants import *
from helper import *
import random
from enum import Enum
from collections import deque # For efficient agent history logs

class Agent:
    """Represents an agent in the simulation."""
    def __init__(self, id, x, y, agent_llm_config):
        self.id = id
        self.x = x
        self.y = y
        self.hp = AGENT_MAX_HP
        self.max_hp = AGENT_MAX_HP
        self.resource_level = AGENT_MAX_RESOURCES / 2
        self.base_strength = random.randint(*AGENT_BASE_STRENGTH_RANGE)
        self.base_defense = random.randint(*AGENT_BASE_DEFENSE_RANGE)
        self.base_fighting_ability = random.randint(*AGENT_BASE_FIGHTING_ABILITY_RANGE)
        self.group_id = None
        self.current_action = {'action': ActionType.IDLE, 'target': None}
        self.ticks_since_last_llm_decision = random.randint(0, 5) # Stagger initial decisions
        self.in_combat_with_group = None # ID of group fighting
        self.in_combat_with_agent = None # ID of agent fighting
        self.pending_group_request_to = None # Agent ID this agent wants to group with
        self.pending_group_requests_from = set() # Agent IDs wanting to group with this agent
        self.simulation_time_step = 0 # Updated by manager before decision request
        self.is_waiting_for_llm = False # Flag if currently waiting for a response
        self.harvest_rate = random.randint(*AGENT_HARVEST_RATE_RANGE)
        self.known_resources = {}
        color_index = self.id % len(AGENT_INITIAL_COLORS) # Cycle through predefined colors
        self.color = AGENT_INITIAL_COLORS[color_index]
        self.group_request_pending_decision = False # Signal to prioritize group decision


        # Agent-specific history log (limited size)
        self.history_log = deque(maxlen=AGENT_MAX_LOG_ENTRIES)

        self.llm_config = agent_llm_config
        self.current_plan = {'plan': PlanType.IDLE, 'target': None, 'path': None}
        self.is_waiting_for_llm = False # Still useful to prevent spamming requests
        
    
        log_msg = f"Created at ({self.x},{self.y}). Rate:{self.harvest_rate}, Color:{self.color}."
        if self.llm_config:
            log_msg += f" Using LLM Config ID: {self.llm_config.get('id', 'N/A')}"
        else:
             log_msg += " No LLM Config assigned!"

        self.visited_trail = deque(maxlen=MAX_TRAIL_LENGTH)
        # Store initial position
        self.visited_trail.append(((self.x, self.y), self.simulation_time_step))

        # Initial log message upon creation
        log_agent_event(self.id, f"Created at ({self.x},{self.y}) Stats: S={self.base_strength}, D={self.base_defense}, F={self.base_fighting_ability}", self)

    @property
    def in_combat(self):
        """Returns True if the agent is currently in combat with anyone."""
        return self.in_combat_with_group is not None or self.in_combat_with_agent is not None

    def is_alive(self):
        """Returns True if the agent has HP above 0."""
        return self.hp > 0

    def take_damage(self, amount, source_info="Unknown"):
        """Applies damage to the agent's HP."""
        if not self.is_alive(): return
        damage_taken = max(0, amount)
        self.hp -= damage_taken
        self.hp = max(0, self.hp)
        log_agent_event(self.id, f"Took {damage_taken:.1f} damage from {source_info}. HP left: {self.hp:.1f}", self)
        if not self.is_alive():
             log_agent_event(self.id, "Died.", self)

    def consume_resource(self):
        """Consumes resources each tick, applies starvation if necessary."""
        if self.is_alive():
            old_res = self.resource_level
            self.resource_level -= AGENT_CONSUMPTION_RATE
            self.resource_level = max(0, self.resource_level)
            if self.resource_level <= 0 and old_res > 0:
                 log_agent_event(self.id, "Ran out of resources and is now starving.", self)
            if self.resource_level <= 0:
                self.take_damage(STARVATION_DAMAGE_PER_TICK, source_info="Starvation")

    def collect_resource(self, amount):
        """Increases agent's resource level."""
        self.resource_level += amount
        self.resource_level = min(AGENT_MAX_RESOURCES, self.resource_level)

    def clear_pending_group_requests(self):
        """Clears outgoing and incoming group requests for this agent."""
        if self.pending_group_request_to is not None:
              log_agent_event(self.id, f"Cleared outgoing group request to {self.pending_group_request_to}.", self, level=logging.DEBUG)
              self.pending_group_request_to = None
        if self.pending_group_requests_from:
              log_agent_event(self.id, f"Cleared incoming group requests from {self.pending_group_requests_from}.", self, level=logging.DEBUG)
              self.pending_group_requests_from.clear()
        # --- VVV Also clear flag VVV ---
        self.group_request_pending_decision = False
        # --- ^^^ Also clear flag ^^^ ---


    def set_new_plan(self, plan_dict):
        """Updates the agent's plan and logs it, clearing wait flags."""
        if not self.is_alive(): return
        # Validate plan_dict has expected keys
        if 'plan' not in plan_dict:
             log_agent_event(self.id, f"Invalid plan dict passed to set_new_plan: {plan_dict}", level=logging.ERROR)
             # Default to IDLE to prevent errors
             self.current_plan = {'plan': PlanType.IDLE, 'target': None, 'path': None}
        else:
             self.current_plan = plan_dict
             if 'target' not in self.current_plan: self.current_plan['target'] = None
             if 'path' not in self.current_plan: self.current_plan['path'] = None

        log_agent_event(self.id, f"Set Plan: {self.current_plan['plan'].name}, Target: {self.current_plan.get('target')}", self)
        self.is_waiting_for_llm = False # Got a plan, no longer waiting


    # Inside Agent class:
    def get_state_for_llm(self, grid_manager, agent_manager, group_manager, resource_manager):
        """Generates the context prompt string for the LLM, with enhanced guidance."""

        is_group_decision = self.group_request_pending_decision or self.current_plan['plan'] == PlanType.RESPOND_TO_GROUP_REQUEST
        requester_id = None
        requester_agent = None
        if is_group_decision and self.pending_group_requests_from:
             # Focus on the first pending request for now
             requester_id = next(iter(self.pending_group_requests_from))
             requester_agent = agent_manager.get_agent(requester_id)


        # --- Agent Status & Affiliation (Same as before) ---
        context = f"You are Agent {self.id}. Current Tick: {self.simulation_time_step}\n"
        context += f"Status: HP={int(self.hp)}/{self.max_hp}, Resources={int(self.resource_level)}, Position=({self.x},{self.y}).\n"
        if self.group_id:
            group = group_manager.get_group(self.group_id)
            if group: context += f"Affiliation: Member of Group {self.group_id} (...stats...).\n" # Keep stats brief?
            else: context += f"Affiliation: Error - Invalid Group ID {self.group_id}. Assume ungrouped.\n"; self.group_id = None
        else: context += "Affiliation: Ungrouped.\n"

        # --- Combat & Grouping Status (Same as before) ---
        if self.in_combat: context += f"Combat Status: Fighting {'Group '+str(self.in_combat_with_group) if self.in_combat_with_group is not None else 'Agent '+str(self.in_combat_with_agent)}.\n"
        else: context += "Combat Status: Not in combat.\n"
        if self.pending_group_request_to: context += f"Grouping Status: You requested to group with Agent {self.pending_group_request_to}. Waiting.\n"
        if self.pending_group_requests_from: req_list = ", ".join(map(str, self.pending_group_requests_from)); context += f"Grouping Status: Agent(s) {req_list} want(s) to group with you.\n"

        # --- Perception (Same as before) ---
        visible_radius = PERCEPTION_RADIUS # <<< Use constant
        visible_objects = grid_manager.get_objects_in_radius(self.x, self.y, visible_radius)
        context += f"Perception (Radius {visible_radius}):\n"

        found_obj_strs = [] # List to hold formatted strings of visible objects
        # Lists needed for determining possible plans later
        nearby_ungrouped_agents_for_prompt = [] # Store IDs of adjacent ungrouped agents
        nearby_groups = {} # Store group_id -> list of agent IDs seen nearby
        nearby_agents = {} # Store agent_id -> distance seen nearby

        for obj, pos in visible_objects:
            # Skip self in perception
            if obj == self: continue

            # Calculate distance
            dist = manhattan_distance((self.x, self.y), pos)

            # Handle visible Agents
            if isinstance(obj, Agent):
                # Store distance for potential attack plan check
                nearby_agents[obj.id] = dist
                # Format basic agent info string
                obj_str = f"  - Agent {obj.id} at {pos} (Dist: {dist}, HP: {int(obj.hp)})"
                if obj.group_id:
                    # Agent belongs to a group
                    other_group = group_manager.get_group(obj.group_id)
                    if other_group:
                        group_size = len(other_group.member_ids)
                        obj_str += f", Group: {obj.group_id} (Size: {group_size})"
                        # Track nearby groups and their members seen
                        if obj.group_id not in nearby_groups: nearby_groups[obj.group_id] = []
                        nearby_groups[obj.group_id].append(obj.id)
                    else: # Agent has invalid group ID
                        obj_str += f", Group: {obj.group_id} (Invalid?)"
                elif obj.id != self.id: # Agent is ungrouped and not self
                    obj_str += ", Ungrouped"
                    # Check if adjacent for grouping possibility
                    if dist <= 1:
                         nearby_ungrouped_agents_for_prompt.append(obj.id)
                # Add the formatted string to our list
                found_obj_strs.append(obj_str)

            # Handle visible Resources
            elif isinstance(obj, tuple) and obj[0] == 'Resource':
                res_info = obj[1] # Get the resource details dict
                res_type = res_info.get('type', 'Unknown')
                res_quantity = res_info.get('quantity', 0)
                # Describe quantity qualitatively
                qty_desc = "High" if res_quantity > RESOURCE_MAX_QUANTITY * 0.6 else ("Medium" if res_quantity > RESOURCE_MAX_QUANTITY * 0.2 else "Low")
                # Format resource string including estimated steps (distance)
                obj_str = f"  - Resource '{res_type}' ({qty_desc} qty) at {pos}, Est. Time: {dist} steps"
                found_obj_strs.append(obj_str)

        # Add the formatted perception strings to the main context
        if not found_obj_strs:
            context += "  - Nothing significant nearby.\n"
        else:
            # Sort by distance? Optional, but might help LLM focus.
            # found_obj_strs.sort(key=lambda s: int(s.split('(Dist: ')[1].split(')')[0]) if '(Dist: ' in s else 999)
            context += "\n".join(found_obj_strs) + "\n"


        # --- Known Resources (Same as before) ---
        context += "**Known Resources (From Memory/Group):**\n"
        res_source = self.known_resources # Default to own knowledge
        if self.group_id is not None:
            group = group_manager.get_group(self.group_id)
            if group:
                res_source = group.group_known_resources # Use group knowledge if grouped

        # Format known resources from the determined source
        agent_known_resources_list = []
        if res_source:
            for pos, info in res_source.items():
                # Recalculate distance for prompt relevance
                if info.get('last_seen_quantity', 0) > 0:
                    est_time = manhattan_distance((self.x, self.y), pos)
                    agent_known_resources_list.append({
                        'type': info.get('type', 'Unknown'),
                        'pos': pos,
                        'est_time': est_time,
                        'last_seen_tick': info.get('last_seen_tick', -1),
                        'last_seen_quantity': info.get('last_seen_quantity', 0)
                    })
            # Sort by estimated time for the prompt
            agent_known_resources_list.sort(key=lambda r: r['est_time'])

        if not agent_known_resources_list:
            context += "  - None currently known.\n"
        else:
             for res in agent_known_resources_list[:5]: # Show nearest 5
                  seen_ago = self.simulation_time_step - res['last_seen_tick'] if res['last_seen_tick'] >= 0 else '?'
                  context += f"  - '{res['type']}' at {res['pos']}, Est. Time: {res['est_time']} steps (Seen {seen_ago} ticks ago)\n"
        
        # --- ^^^ Use Agent/Group Specific Knowledge ^^^ ---
        if is_group_decision and requester_agent:
            # --- Focused Group Decision Prompt ---
            context += "**ACTION REQUIRED: Respond to Group Request**\n"
            context += f"Agent {requester_id} at ({requester_agent.x},{requester_agent.y}) (HP: {int(requester_agent.hp)}) wants to form a group with you.\n"
            context += "**Review Rules:**\n"
            context += "- You MUST be UNGROUPED and adjacent to Accept.\n"
            context += "- Benefits: Combined strength, shared resource knowledge.\n"
            context += "- Risks: Requester might be weak, competition if you prefer being alone.\n"
            context += "**Decision Options:**\n"
            possible_plans = [
                f"ACCEPT_GROUP_FROM {requester_id}",
                f"ATTACK_AGENT {requester_id}",
                "IDLE (Implicitly Ignore/Reject)"
            ]
            context += f"Available Plans NOW: {', '.join(possible_plans)}\n"
            context += 'Evaluate the situation. Choose ONLY ONE plan from the list above.\n'
            context += 'Output JSON like: {"plan": "PLAN_NAME", "target": <agent_id_or_null>}\n'
            context += 'Examples: {"plan": "ACCEPT_GROUP_FROM", "target": 1}, {"plan": "ATTACK_AGENT", "target": 1}, {"plan": "IDLE"}\n'

        else:
            # --- VVV MODIFIED GUIDANCE FOR PLANS VVV ---
            context += "**Goal & Planning Rules:**\n"
            context += "1. Goal: Ensure long-term SURVIVAL. Balance resources, safety/strength (grouping), eliminating competition, and exploration.\n"
            context += "2. Movement: Use MOVE <Direction> to reach targets step-by-step.\n"
            context += "3. Resource PRECONDITION: MUST be AT the resource location to COLLECT.\n"
            context += "4. Grouping PRECONDITION: MUST be adjacent (Dist 1) and UNGROUPED to FORM/ACCEPT group. You CANNOT accept if already grouped.\n"

            # 5. Added Rule - Evaluating Group Requests:
            context += "5. Evaluating Group Requests/Joining: If ACCEPT_GROUP_FROM or FORM_GROUP_WITH is available:\n"
            context += "   - BENEFITS: Increases strength/defense, **gain knowledge of new resource locations from others**.\n" # <<< Benefit added
            context += "   - CONSIDER: Requester/Target HP (Perception)? Your own status (Alone/Low HP)?\n"
            context += "   - ALTERNATIVE: ATTACK weak agents to eliminate resource competition.\n"
            context += "   - CHOICE: Decide whether to ACCEPT, ATTACK, or IGNORE (choose IDLE/other plan).\n"

            context += "6. Combat PRECONDITION: ATTACK only if target is nearby and strategically sound (consider HP of self vs target).\n"
            context += "7. Planning: Choose a high-level plan. System handles step-by-step movement.\n"
            # --- ^^^ END OF REVISED RULES ^^^ ---

            # --- Dynamically List Possible Plans with Context ---
            possible_plans = ["IDLE", "EXPLORE <optional_direction>"] # Basic plans
            current_pos_tuple = (self.x, self.y)

            # Resource Plan (Go To Nearest)
            nearest_non_empty_res = min(agent_known_resources_list, key=lambda r: r['est_time']) if agent_known_resources_list else None
            if nearest_non_empty_res:
                target_coords_str = f"[{nearest_non_empty_res['pos'][0]},{nearest_non_empty_res['pos'][1]}]"
                seen_ago = self.simulation_time_step - nearest_non_empty_res['last_seen_tick'] if nearest_non_empty_res['last_seen_tick'] >= 0 else '?'
                plan_context = f"(Nearest Known>0, {nearest_non_empty_res['est_time']} steps, Seen {seen_ago} ago)"
                possible_plans.append(f"GO_TO_RESOURCE {target_coords_str} {plan_context}")

            # Grouping Plans
            if self.group_id is None:
                # FORM (using list populated during perception)
                if nearby_ungrouped_agents_for_prompt:
                    # Maybe suggest the closest one found?
                    closest_ungrouped_id = nearby_ungrouped_agents_for_prompt[0] # Assuming perception loop finds closest first or list is small
                    possible_plans.append(f"FORM_GROUP_WITH {closest_ungrouped_id} (Adjacent)")
                # ACCEPT (using agent's internal state)
                if self.pending_group_requests_from:
                    requester_id = next(iter(self.pending_group_requests_from))
                    possible_plans.append(f"ACCEPT_GROUP_FROM {requester_id}")

            # Combat Plans
            potential_attack_targets = []
            # Check nearby agents that are NOT in the same group (if grouped)
            for agent_id, dist in nearby_agents.items():
                if dist <= 1: # Consider adjacent agents for attack
                    target_agent = agent_manager.get_agent(agent_id)
                    if target_agent:
                        # Allow attacking ungrouped agents, or agents in different groups
                        if target_agent.group_id is None or (self.group_id is not None and target_agent.group_id != self.group_id):
                            potential_attack_targets.append(f"ATTACK_AGENT {agent_id}")

            # Check nearby groups (that are not own group)
            for group_id, member_ids_seen in nearby_groups.items():
                if self.group_id != group_id: # Can't attack own group
                    potential_attack_targets.append(f"ATTACK_GROUP {group_id}")

            # Add first potential attack target to possible plans for brevity in prompt
            if potential_attack_targets:
                possible_plans.append(potential_attack_targets[0]) # e.g., "ATTACK_AGENT 1" or "ATTACK_GROUP 0"

            # Format the list of possible plans clearly
            possible_plans_str = ", ".join(list(set(possible_plans))) # Use set to remove duplicates
            context += f"**Available Plans NOW:** {possible_plans_str}\n"

            # --- Final Instruction ---
            context += 'Evaluate your situation using Perception and Rules. Choose the best plan for SURVIVAL.\n'
            context += 'Think and rationalize about your best course of action. Be detailed and think it through.\n'
            context += 'Next, output JSON like: {"rationale:" <rationale_for_choosing_plan>, "plan": "PLAN_NAME", "target": <target_details_or_null>}\n'
            context += 'Examples: '
            context += '{"rationale": "This agent is starving and must find resources for survival.", "plan": "GO_TO_RESOURCE", "target": [5, 4]},'
            context += '{"rationale": "This agent is currently solo. The shared knowledge and strength of a group will increase chances of survival.", "plan": "ACCEPT_GROUP_FROM", "target": 1},'
            context += '{"rationale": "This agent is weak and has significant resources. Instead of grouping with it, it will be preferable to attack it and take its resources for my own survival.", "plan": "ATTACK_AGENT", "target": 1}, {"plan": "IDLE"}\n'
            context += 'Your plan JSON: '

        return context

    def set_new_action(self, action_dict):
        """Updates the agent's action and related state after receiving LLM result."""
        if not self.is_alive(): return # Safety check

        self.current_action = action_dict
        log_agent_event(self.id, f"Set Action: {self.current_action['action'].name}, Target: {self.current_action['target']}", self)

        # Update internal state based on *newly decided* action
        if self.current_action['action'] != ActionType.FORM_GROUP:
             if self.pending_group_request_to is not None:
                  log_agent_event(self.id, f"Cancelled outgoing group request to {self.pending_group_request_to} due to new action.", self, level=logging.DEBUG)
                  # Target's incoming request expires naturally via update_pending_requests
                  self.pending_group_request_to = None

        # Set tentative combat flags (CombatManager confirms/initiates)
        if self.current_action['action'] == ActionType.ATTACK_GROUP: self.in_combat_with_group = self.current_action['target']; self.in_combat_with_agent = None
        elif self.current_action['action'] == ActionType.ATTACK_AGENT: self.in_combat_with_agent = self.current_action['target']; self.in_combat_with_group = None
        # Don't clear flags on non-attack actions, agent might be *under* attack.

        self.is_waiting_for_llm = False # No longer waiting