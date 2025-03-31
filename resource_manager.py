from constants import *
from helper import *
import random

class ResourceManager:
    """Manages resources (spawning, collection) in the simulation."""
    def __init__(self, grid_manager):
        self.grid_manager = grid_manager
        self.resources = {} # (x, y) -> {'type': 'food', 'quantity': N} map
        self.consumption_rate = AGENT_CONSUMPTION_RATE
        logging.info("ResourceManager initialized.")

    def spawn_resources(self, num_to_spawn):
        """Spawns a number of new resource deposits at random empty locations."""
        spawned_count = 0
        for _ in range(num_to_spawn):
            pos = self.grid_manager.get_random_empty_cell()
            if pos:
                quantity = random.randint(RESOURCE_MAX_QUANTITY // 2, RESOURCE_MAX_QUANTITY)
                res_info = {'type': 'food', 'quantity': quantity}
                self.resources[pos] = res_info
                # Place a marker on the grid for rendering and interaction detection
                # Use a tuple: ('Resource', dict_with_info) to distinguish from Agent objects
                self.grid_manager.place_object(('Resource', res_info), pos[0], pos[1])
                spawned_count += 1
        if spawned_count > 0:
             logging.info(f"Spawned {spawned_count} new resource deposits.")

    def collect_resource(self, agent, x, y):
        """Allows an agent to collect resources from a specific location."""
        pos = (x,y)
        if pos in self.resources:
            res_info = self.resources[pos]
            # Determine amount to collect (up to resource availability and agent capacity?)
            # Simple: Collect fixed amount or remaining, whichever is less
            collect_amount = min(res_info['quantity'], RESOURCE_COLLECT_AMOUNT)
            agent.collect_resource(collect_amount) # Update agent's internal resources
            res_info['quantity'] -= collect_amount # Decrease resource quantity

            # Log the event using the central function
            log_agent_event(agent.id, f"Collected {collect_amount:.1f} {res_info['type']} at {pos}. Deposit left: {res_info['quantity']:.1f}", agent)

            # Check if resource deposit is depleted
            if res_info['quantity'] <= 0:
                logging.info(f"Resource deposit at {pos} depleted.")
                del self.resources[pos] # Remove from manager's dict
                # Also remove the marker from the grid
                resource_marker = ('Resource', res_info) # Recreate marker tuple used for placing
                self.grid_manager.remove_object(resource_marker, x, y)
            return True
        # Log failure if agent tried to collect where there was nothing
        log_agent_event(agent.id, f"Attempted collection at {pos}, but no resource found.", agent, level=logging.WARNING)
        return False

    def get_resource_locations(self):
        """Returns a dictionary of current resource locations and info."""
        # Return a shallow copy to prevent external modification
        return dict(self.resources)

    def periodic_spawn(self):
        """Randomly spawns new resources based on the spawn rate."""
        if random.random() < RESOURCE_SPAWN_RATE:
              self.spawn_resources(1)

    def harvest_resource_at(self, agent, pos_tuple, amount_to_harvest):
        """Allows an agent to harvest a specific amount from a location."""
        # pos = (x,y) # Already passed as tuple
        if pos_tuple in self.resources:
            res_info = self.resources[pos_tuple]

            # Ensure we don't harvest more than available
            actual_harvest_amount = min(amount_to_harvest, res_info['quantity'])

            if actual_harvest_amount <= 0: # Resource might be empty but not deleted yet
                 log_agent_event(agent.id, f"Attempted harvest at {pos_tuple}, but resource is empty.", agent, level=logging.WARNING)
                 return False # Indicate nothing was harvested

            # Update agent's resources
            agent.collect_resource(actual_harvest_amount)
            # Decrease resource quantity at location
            res_info['quantity'] -= actual_harvest_amount

            log_agent_event(agent.id, f"Harvested {actual_harvest_amount:.1f} {res_info['type']} at {pos_tuple}. Deposit left: {res_info['quantity']:.1f}", agent)

            # Check if resource deposit is now depleted
            if res_info['quantity'] <= 0:
                logging.info(f"Resource deposit at {pos_tuple} depleted.")
                # Remove from manager's dict
                del self.resources[pos_tuple]
                # Also remove the marker from the grid
                # Recreate marker tuple used for placing: ('Resource', dict_info)
                # Need to find the exact marker object on the grid to remove it
                objects_at_pos = self.grid_manager.get_objects_at(pos_tuple[0], pos_tuple[1])
                marker_to_remove = None
                for obj in objects_at_pos:
                     # Check if it's the resource marker tuple associated with the depleted info
                     if isinstance(obj, tuple) and obj[0] == 'Resource' and obj[1] is res_info:
                          marker_to_remove = obj
                          break
                if marker_to_remove:
                     self.grid_manager.remove_object(marker_to_remove, pos_tuple[0], pos_tuple[1])
                else:
                     logging.warning(f"Could not find resource marker on grid at {pos_tuple} to remove after depletion.")

            return True # Indicate successful harvest attempt (even if partial)
        else: # Resource position no longer valid
            log_agent_event(agent.id, f"Attempted harvest at {pos_tuple}, but no resource found (already depleted?).", agent, level=logging.WARNING)
            return False # Indicate failure