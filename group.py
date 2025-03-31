from constants import *
from helper import *

class Group:
    """Represents a group of agents acting together."""
    def __init__(self, id, initial_member_id, agent_manager, initiator_color):
        self.id = id
        self.member_ids = {initial_member_id} # Set of agent IDs
        self.agent_manager = agent_manager # Reference to access agent objects
        self.group_strength = 0
        self.group_defense = 0
        self.group_fighting_ability = 0
        self.total_hp = 0
        self.in_combat_with_group = None # ID of the group being fought
        self.in_combat_with_agent = None # ID of lone agent being fought

        self.color = initiator_color # Set group color from initiator
        self.group_known_resources = {}

         # Initial setup for the first member
        initial_agent = self.agent_manager.get_agent(initial_member_id)
        if initial_agent:
            initial_agent.group_id = self.id
            initial_agent.color = self.color
            initial_agent.clear_pending_group_requests()
            # --- VVV Initialize group memory & update member VVV ---
            # Start group memory with the initial member's knowledge
            self.group_known_resources = initial_agent.known_resources.copy()
            # Ensure the agent also has the (potentially updated) group view now
            initial_agent.known_resources = self.group_known_resources.copy()
            # --- ^^^ Initialize group memory & update member ^^^ ---
        self.update_stats()
        logging.info(f"Group {self.id} created with initial member Agent {initial_member_id}.")

    @property
    def in_combat(self):
        """Returns True if the group is currently in combat."""
        return self.in_combat_with_group is not None or self.in_combat_with_agent is not None

    def add_member(self, agent_id):
        """Adds an agent to the group if they are valid and ungrouped."""
        agent = self.agent_manager.get_agent(agent_id)
        if agent and agent.is_alive() and agent.group_id is None:
            self.member_ids.add(agent_id)
            agent.group_id = self.id
            agent.color = self.color # Update agent's color to group color
            agent.clear_pending_group_requests()

            # --- VVV Merge Knowledge VVV ---
            # Merge joining agent's knowledge into the group's knowledge
            # Simple update: new info overwrites old if key exists
            self.group_known_resources.update(agent.known_resources)
            # Update the joining agent's knowledge to the latest group view
            agent.known_resources = self.group_known_resources.copy()
            # NOTE: Existing members do NOT automatically get the new agent's unique knowledge
            # unless they perceive the same resources later or another merge happens.
            # --- ^^^ Merge Knowledge ^^^ ---

            self.update_stats()
            log_agent_event(agent_id, f"Joined Group {self.id} (Color: {self.color}).", agent)
            return True
        return False

    def remove_member(self, agent_id):
        """Removes an agent from the group (e.g., if they die or leave)."""
        if agent_id in self.member_ids:
            self.member_ids.remove(agent_id)
            agent = self.agent_manager.get_agent(agent_id)
            if agent:
                 log_agent_event(agent_id, f"Removed from Group {self.id}.", agent)
                 agent.group_id = None
                 agent.in_combat_with_group = None; agent.in_combat_with_agent = None # Reset combat state
            else:
                logging.info(f"Agent {agent_id} already gone when removing from Group {self.id}.")

            if not self.member_ids: pass # GroupManager handles disbanding empty groups
            else: self.update_stats() # Update stats if members remain


    def get_member_agents(self):
        """Returns a list of valid, living agent objects currently in the group."""
        members = []
        current_member_ids = list(self.member_ids) # Iterate over copy for safe removal
        ids_updated = False
        for agent_id in current_member_ids:
             agent = self.agent_manager.get_agent(agent_id)
             if agent and agent.is_alive():
                 # Ensure agent's group_id is still correct
                 if agent.group_id == self.id:
                     members.append(agent)
                 else: # Agent's group ID changed somehow, remove from this group
                     logging.warning(f"Agent {agent_id} had inconsistent group ID ({agent.group_id}), expected {self.id}. Removing.")
                     self.member_ids.remove(agent_id)
                     ids_updated = True
             elif agent_id in self.member_ids: # Agent died or is missing, remove ID
                 logging.debug(f"Removing dead/missing Agent {agent_id} from Group {self.id} member list.")
                 self.member_ids.remove(agent_id)
                 ids_updated = True

        if ids_updated: self.update_stats() # Recalculate stats if members changed
        return members

    def update_stats(self):
        """Recalculates the group's combined stats based on current members."""
        # Use get_member_agents() to ensure we only count living/valid members
        members = self.get_member_agents()
        if not members:
            self.group_strength = 0; self.group_defense = 0
            self.group_fighting_ability = 0; self.total_hp = 0; return

        self.group_strength = sum(a.base_strength for a in members)
        self.group_defense = sum(a.base_defense for a in members)
        self.group_fighting_ability = sum(a.base_fighting_ability for a in members)
        self.total_hp = sum(a.hp for a in members)
        logging.debug(f"Group {self.id} stats updated: Count={len(members)}, Str={self.group_strength}, Def={self.group_defense}, Fight={self.group_fighting_ability}, HP={self.total_hp:.1f}")

    def get_centroid(self):
        """Calculates the average position (centroid) of the group members."""
        members = self.get_member_agents()
        if not members: return None
        avg_x = sum(a.x for a in members) / len(members)
        avg_y = sum(a.y for a in members) / len(members)
        return (int(round(avg_x)), int(round(avg_y))) # Round to nearest int for grid