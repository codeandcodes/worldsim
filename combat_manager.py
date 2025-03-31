from constants import *
from helper import *
import random

class CombatManager:
    """Handles combat initiation and resolution between agents and/or groups."""
    def __init__(self, agent_manager, group_manager):
        self.agent_manager = agent_manager
        self.group_manager = group_manager
        # Add self reference to agent manager for bidirectional communication if needed later
        self.agent_manager.combat_manager = self
        logging.info("CombatManager initialized.")

    def get_combat_participants(self, id1, id2):
        """Gets agent/group objects and their types ('agent'/'group') given IDs."""
        p1 = self.agent_manager.get_agent(id1); type1 = 'agent' if p1 else None
        p2 = self.agent_manager.get_agent(id2); type2 = 'agent' if p2 else None
        # If not found as agent, try finding as group
        if not p1: p1 = self.group_manager.get_group(id1); type1 = 'group' if p1 else None
        if not p2: p2 = self.group_manager.get_group(id2); type2 = 'group' if p2 else None
        return p1, type1, p2, type2

    def initiate_combat(self, initiator_id, target_id):
        """Initiates combat between two entities (agent or group)."""
        if initiator_id == target_id: return False # Cannot fight self

        p1, type1, p2, type2 = self.get_combat_participants(initiator_id, target_id)

        # --- Validation ---
        if not p1 or not p2:
             logging.warning(f"Combat initiation failed: Participant {initiator_id if not p1 else target_id} not found.")
             return False
        # Check if participants are alive / have members
        if (type1 == 'agent' and not p1.is_alive()) or (type1 == 'group' and not p1.get_member_agents()):
             logging.warning(f"Combat initiation failed: Initiator {type1} {initiator_id} is invalid/empty."); return False
        if (type2 == 'agent' and not p2.is_alive()) or (type2 == 'group' and not p2.get_member_agents()):
             logging.warning(f"Combat initiation failed: Target {type2} {target_id} is invalid/empty."); return False

        # Check if they are already fighting each other (no change needed)
        # (Complex check covering all agent/group vs agent/group combinations)
        is_already_fighting = False
        if type1 == 'agent' and ((type2 == 'agent' and p1.in_combat_with_agent == p2.id) or (type2 == 'group' and p1.in_combat_with_group == p2.id)): is_already_fighting = True
        if type1 == 'group' and ((type2 == 'agent' and p1.in_combat_with_agent == p2.id) or (type2 == 'group' and p1.in_combat_with_group == p2.id)): is_already_fighting = True
        if is_already_fighting: logging.debug(f"{type1} {initiator_id} is already fighting {type2} {target_id}."); return True

        # Check for fighting own group/members (prevent friendly fire initiation)
        if type1 == 'agent' and type2 == 'group' and p1.group_id == p2.id: logging.warning(f"Combat fail: Agent {p1.id} cannot attack own group {p2.id}."); return False
        if type1 == 'group' and type2 == 'agent' and p2.group_id == p1.id: logging.warning(f"Combat fail: Group {p1.id} cannot attack own member {p2.id}."); return False
        if type1 == 'agent' and type2 == 'agent' and p1.group_id is not None and p1.group_id == p2.group_id: logging.warning(f"Combat fail: Agents {p1.id} and {p2.id} in same group."); return False
        if type1 == 'group' and type2 == 'group' and p1.id == p2.id: logging.warning(f"Combat fail: Group {p1.id} cannot attack itself."); return False


        # --- Initiate Combat ---
        logging.info(f"Combat initiated: {type1.capitalize()} {initiator_id} vs {type2.capitalize()} {target_id}")

        # End any previous combats for both participants first
        self.end_combat_for_participant(p1, type1, reason="Engaging new opponent")
        self.end_combat_for_participant(p2, type2, reason="Engaging new opponent")

        # Set new combat states (mutual)
        opponent1_id = target_id
        opponent2_id = initiator_id
        if type1 == 'agent': p1.in_combat_with_agent = opponent1_id if type2 == 'agent' else None; p1.in_combat_with_group = opponent1_id if type2 == 'group' else None
        if type1 == 'group': p1.in_combat_with_agent = opponent1_id if type2 == 'agent' else None; p1.in_combat_with_group = opponent1_id if type2 == 'group' else None
        if type2 == 'agent': p2.in_combat_with_agent = opponent2_id if type1 == 'agent' else None; p2.in_combat_with_group = opponent2_id if type1 == 'group' else None
        if type2 == 'group': p2.in_combat_with_agent = opponent2_id if type1 == 'agent' else None; p2.in_combat_with_group = opponent2_id if type1 == 'group' else None

        # Mark agents within groups as in combat (redundant? agent state should reflect group state)
        # This ensures individual agents know their combat target type/id
        if type1 == 'group':
             for ag in p1.get_member_agents(): ag.in_combat_with_group = opponent1_id if type2 == 'group' else None; ag.in_combat_with_agent = opponent1_id if type2 == 'agent' else None
        if type2 == 'group':
            for ag in p2.get_member_agents(): ag.in_combat_with_group = opponent2_id if type1 == 'group' else None; ag.in_combat_with_agent = opponent2_id if type1 == 'agent' else None

        return True

    def get_combat_stats(self, participant, p_type):
        """Helper to get standardized combat stats for an agent or group."""
        if p_type == 'agent':
            if participant and participant.is_alive():
                 # Treat agent as group of 1 for combat calculations
                 return {'hp': participant.hp, 'strength': participant.base_strength,
                         'defense': participant.base_defense, 'fighting': participant.base_fighting_ability,
                         'member_count': 1, 'id': participant.id} # Add ID for logging clarity
            else: return None # Agent invalid or dead
        elif p_type == 'group':
             # Ensure group exists and has living members
             if participant and participant.get_member_agents():
                 participant.update_stats() # Ensure stats are current
                 return {'hp': participant.total_hp, 'strength': participant.group_strength,
                         'defense': participant.group_defense, 'fighting': participant.group_fighting_ability,
                         'member_count': len(participant.member_ids), 'id': participant.id} # Add ID
             else: return None # Group invalid or empty
        return None # Invalid type

    def resolve_combat_round(self, p1, type1, p2, type2):
        """Resolves one round of damage exchange between two participants."""
        # Get current stats, returns None if participant invalid/empty
        stats1 = self.get_combat_stats(p1, type1)
        stats2 = self.get_combat_stats(p2, type2)

        # If one side is gone before the round starts, end combat
        if not stats1 or not stats2:
             logging.info(f"Combat ending between {type1} {p1.id if p1 else '?'} and {type2} {p2.id if p2 else '?'} - participant invalid/eliminated before round.")
             self.end_combat(p1, type1, p2, type2, reason="Participant eliminated before round")
             return

        logging.debug(f"Combat Round: {type1} {stats1['id']} (Fight:{stats1['fighting']:.0f}) vs {type2} {stats2['id']} (Fight:{stats2['fighting']:.0f})")

        # --- Damage Calculation Helper ---
        def calculate_damage(attacker_stats, defender_stats):
            random_factor = random.uniform(0.8, 1.2) # Add randomness
            # Use fighting ability for attack potential, defense stat for reduction
            attack_power = attacker_stats['fighting']
            defense_power = defender_stats['defense']
            damage = max(0, (attack_power * random_factor) - defense_power)
            logging.debug(f"  -> {attacker_stats['id']} deals {damage:.1f} (BaseAtk:{attack_power:.0f}*Rand:{random_factor:.2f} vs Def:{defense_power:.0f})")
            return damage

        # --- Apply Damage Helper ---
        def apply_damage(target_participant, target_type, total_damage, source_info):
            if target_type == 'agent':
                # Apply directly to the agent
                target_participant.take_damage(total_damage, source_info=source_info)
            elif target_type == 'group':
                # Distribute damage among living members
                members = target_participant.get_member_agents() # Get current living members
                if not members: return # Group might be empty now
                damage_per_agent = total_damage / len(members) if members else 0
                logging.debug(f"  Distributing {damage_per_agent:.1f} dmg each to {len(members)} members of Group {target_participant.id}")
                for agent in members:
                    agent.take_damage(damage_per_agent, source_info=source_info) # Agent logs damage taken

        # Calculate and Apply Damage (Simulated simultaneous exchange)
        damage_1_to_2 = calculate_damage(stats1, stats2)
        damage_2_to_1 = calculate_damage(stats2, stats1)

        source_info1 = f"{type1.capitalize()} {stats1['id']}"
        source_info2 = f"{type2.capitalize()} {stats2['id']}"
        apply_damage(p2, type2, damage_1_to_2, source_info=source_info1)
        apply_damage(p1, type1, damage_2_to_1, source_info=source_info2)

        # Check for end conditions AFTER damage (deaths processed later by AgentManager)
        # Re-check stats/validity after damage application
        stats1_after = self.get_combat_stats(p1, type1)
        stats2_after = self.get_combat_stats(p2, type2)
        # End if one side is now invalid/empty/dead
        if not stats1_after or not stats2_after:
             self.end_combat(p1, type1, p2, type2, reason="Participant eliminated post-damage")


    def end_combat_for_participant(self, p, p_type, reason=""):
        """Clears combat state flags for a single agent or group members."""
        if not p: return # Participant might already be gone

        opponent_id = None
        if p_type == 'agent':
              opponent_id = p.in_combat_with_agent or p.in_combat_with_group
              p.in_combat_with_agent = None; p.in_combat_with_group = None
              if opponent_id is not None: log_agent_event(p.id, f"Combat ended. Reason: {reason}", p, level=logging.DEBUG)
        elif p_type == 'group':
              opponent_id = p.in_combat_with_agent or p.in_combat_with_group
              p.in_combat_with_agent = None; p.in_combat_with_group = None
              # Also clear flags for all current members
              member_ids = list(p.member_ids) # Copy ids
              for ag_id in member_ids:
                  agent = self.agent_manager.get_agent(ag_id)
                  if agent:
                      agent.in_combat_with_agent = None; agent.in_combat_with_group = None;
                      # Don't log for every member, just the group
              if opponent_id is not None: logging.info(f"Combat ended for Group {p.id}. Reason: {reason}")

    def end_combat(self, p1, type1, p2, type2, reason=""):
        """Cleans up combat state for both participants."""
        # Log the end event - use IDs in case objects are gone
        id1 = p1.id if p1 else '?'
        id2 = p2.id if p2 else '?'
        logging.info(f"Combat ended: {type1} {id1} vs {type2} {id2}. Reason: {reason}")
        # Clear state for both, handling cases where one might be None
        self.end_combat_for_participant(p1, type1, reason)
        self.end_combat_for_participant(p2, type2, reason)


    def resolve_all_combats(self):
        """Iterates through all entities and resolves one round for active combat pairs."""
        processed_pairs = set() # Track (id1, id2) tuples to avoid double processing

        # Combine agents and groups into a single list for easier iteration? No, logic differs.
        # Iterate Agents first
        agent_ids = list(self.agent_manager.agents.keys()) # Copy keys
        for agent_id1 in agent_ids:
            agent1 = self.agent_manager.get_agent(agent_id1)
            if not agent1 or not agent1.is_alive() or not agent1.in_combat: continue

            opponent_id = agent1.in_combat_with_agent or agent1.in_combat_with_group
            if opponent_id is None: # Inconsistent state? Clear it.
                 self.end_combat_for_participant(agent1, 'agent', reason="Inconsistent state (no opponent ID)")
                 continue

            pair = tuple(sorted((agent_id1, opponent_id)))
            if pair in processed_pairs: continue # Already handled this pair

            p1, type1, p2, type2 = self.get_combat_participants(agent_id1, opponent_id)

            if p1 and p2: # Both exist
                 # Check for mutual combat state more robustly
                 p1_fights_p2 = (p1.in_combat_with_agent == p2.id) or (p1.in_combat_with_group == p2.id)
                 p2_fights_p1 = False
                 if type2 == 'agent': p2_fights_p1 = (p2.in_combat_with_agent == p1.id) or (p2.in_combat_with_group == p1.id)
                 if type2 == 'group': p2_fights_p1 = (p2.in_combat_with_agent == p1.id) or (p2.in_combat_with_group == p1.id)

                 if p1_fights_p2 and p2_fights_p1: # They are indeed fighting each other
                     self.resolve_combat_round(p1, type1, p2, type2)
                 else: # Inconsistent state, end the combat link for both
                      logging.warning(f"Inconsistent combat state for pair {pair}. Ending combat.")
                      self.end_combat(p1, type1, p2, type2, reason="Inconsistent state")
                 processed_pairs.add(pair) # Mark pair as processed

            elif p1: # Opponent p2 doesn't exist, end combat for p1
                 logging.info(f"{type1} {p1.id} was in combat with non-existent opponent {opponent_id}.")
                 self.end_combat_for_participant(p1, type1, reason="Opponent disappeared")
                 processed_pairs.add(pair) # Mark as processed

        # Iterate Groups (catches group vs group not initiated by agent check)
        group_ids = list(self.group_manager.groups.keys()) # Copy keys
        for group_id1 in group_ids:
            group1 = self.group_manager.get_group(group_id1)
            # Check if group exists, has members, and is in combat
            if not group1 or not group1.get_member_agents() or not group1.in_combat: continue

            opponent_id = group1.in_combat_with_agent or group1.in_combat_with_group
            if opponent_id is None:  # Inconsistent state
                 self.end_combat_for_participant(group1, 'group', reason="Inconsistent state (no opponent ID)")
                 continue

            pair = tuple(sorted((group_id1, opponent_id)))
            if pair in processed_pairs: continue # Already handled this pair

            p1, type1, p2, type2 = self.get_combat_participants(group_id1, opponent_id)

            # Repeat mutual check and resolve/end logic (similar to agent loop)
            if p1 and p2:
                 p1_fights_p2 = (p1.in_combat_with_agent == p2.id) or (p1.in_combat_with_group == p2.id)
                 p2_fights_p1 = False
                 if type2 == 'agent': p2_fights_p1 = (p2.in_combat_with_agent == p1.id) or (p2.in_combat_with_group == p1.id)
                 if type2 == 'group': p2_fights_p1 = (p2.in_combat_with_agent == p1.id) or (p2.in_combat_with_group == p1.id)

                 if p1_fights_p2 and p2_fights_p1:
                     self.resolve_combat_round(p1, type1, p2, type2)
                 else:
                     logging.warning(f"Inconsistent combat state for pair {pair}. Ending combat.")
                     self.end_combat(p1, type1, p2, type2, reason="Inconsistent state")
                 processed_pairs.add(pair)

            elif p1:
                 logging.info(f"{type1} {p1.id} was in combat with non-existent opponent {opponent_id}.")
                 self.end_combat_for_participant(p1, type1, reason="Opponent disappeared")
                 processed_pairs.add(pair)