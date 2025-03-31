from constants import *
from helper import *
from group import Group

class GroupManager:
    """Manages all groups in the simulation."""
    def __init__(self, agent_manager):
        self.groups = {} # group_id -> Group object
        self.next_group_id = 0
        self.agent_manager = agent_manager
        logging.info("GroupManager initialized.")

    def create_group_with_agents(self, acceptor_id, requester_id): # Renamed params for clarity
        """Creates a new group if both agents are valid and ungrouped."""
        acceptor_agent = self.agent_manager.get_agent(acceptor_id)
        requester_agent = self.agent_manager.get_agent(requester_id) # This is the initiator

        # Check conditions before creating group object
        if acceptor_agent and acceptor_agent.is_alive() and acceptor_agent.group_id is None and \
           requester_agent and requester_agent.is_alive() and requester_agent.group_id is None:
            new_id = self.next_group_id
            initiator_color = requester_agent.color

            # Group init adds acceptor_agent, logs creation, sets group color
            # Pass acceptor_id as initial member, pass initiator's color
            group = Group(new_id, acceptor_id, self.agent_manager, initiator_color)

            # --- VVV Merge Requester's Knowledge & Update Members VVV ---
            # Merge requester's knowledge into the group's knowledge (already has acceptor's)
            # Prioritize newer info if timestamps clash (simple update overwrites)
            group.group_known_resources.update(requester_agent.known_resources)
            # Update BOTH agents' knowledge to the fully merged set
            merged_knowledge = group.group_known_resources.copy() # Copy merged data
            acceptor_agent.known_resources = merged_knowledge
            requester_agent.known_resources = merged_knowledge
            # --- ^^^ Merge Requester's Knowledge & Update Members ^^^ ---

            # Add requester_agent (the second member), logs joining, sets color
            added_ok = group.add_member(requester_id)

            if added_ok:
                self.groups[new_id] = group
                self.next_group_id += 1
                # Clear pending requests (agents clear internally on join/color set)
                # self.agent_manager.clear_pending_requests_involving(acceptor_id) # Done implicitly by agent
                # self.agent_manager.clear_pending_requests_involving(requester_id) # Done implicitly by agent
                log_agent_event(acceptor_id, f"Successfully formed Group {new_id} between Agent {acceptor_id} and Agent {requester_id}. Group color: {initiator_color}")
                log_agent_event(requester_id, f"Successfully formed Group {new_id} between Agent {acceptor_id} and Agent {requester_id}. Group color: {initiator_color}")
                return group
            else:
                 logging.error(f"Failed to add second member {requester_id} to new group {new_id}. Aborting group creation.")
                 acceptor_agent.group_id = None # Undo acceptor's group assignment
                 acceptor_agent.color = acceptor_agent.color # Revert color? No, keep original assigned color.
                 return None
        # Log failure reason
        reason = "one or both agents invalid/dead/already grouped"
        logging.warning(f"Cannot create group between {acceptor_id} and {requester_id}: {reason}.")
        return None

    def add_agent_to_group(self, agent_id, group_id):
        """Adds an agent to an existing group (not typically called directly, used by Group)."""
        group = self.get_group(group_id)
        agent = self.agent_manager.get_agent(agent_id)
        if group and agent and agent.group_id is None:
            return group.add_member(agent_id) # Group.add_member handles logging
        return False

    def remove_agent_from_group(self, agent_id, group_id):
        """Removes agent from group and checks if group should disband."""
        group = self.get_group(group_id)
        if group:
            group.remove_member(agent_id) # Group.remove_member handles logging/state
            # Check immediately if group became empty
            if not group.member_ids:
                self.disband_group(group_id)

    def get_group(self, group_id):
        """Retrieves a group object by its ID."""
        return self.groups.get(group_id)

    def disband_group(self, group_id):
        """Removes an empty group from the simulation."""
        group = self.groups.pop(group_id, None) # Remove from dict
        if group:
            logging.info(f"Group {group_id} disbanded (no members left).")
            # Ensure combat state is cleaned up if group was fighting
            opponent_id = group.in_combat_with_group or group.in_combat_with_agent
            if opponent_id is not None:
                 p1, type1, p2, type2 = self.agent_manager.combat_manager.get_combat_participants(group.id, opponent_id)
                 # Call end_combat to properly clear state on both sides
                 self.agent_manager.combat_manager.end_combat(p1, type1, p2, type2, reason=f"Group {group_id} disbanded")


    def update_all_group_stats(self):
        """Updates stats for all existing groups."""
        for group in self.groups.values():
            group.update_stats() # Group method handles logic

    def manage_groups(self):
        """Performs periodic group maintenance (e.g., disbanding empty groups)."""
        # Find IDs of groups that currently have no members
        empty_group_ids = [id for id, group in self.groups.items() if not group.member_ids]
        for group_id in empty_group_ids:
            self.disband_group(group_id) # Disband them

    def get_all_groups(self):
        """Returns a list of all current group objects."""
        return list(self.groups.values())