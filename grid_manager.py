from constants import *
from helper import *
import random
from agent import Agent

class GridManager:
    """Manages the simulation grid and object positions."""
    def __init__(self, width, height):
        self.width = width
        self.height = height
        # Initialize grid as dict of empty lists for each coordinate
        self.grid = {(x, y): [] for x in range(self.width) for y in range(self.height)}
        logging.info(f"GridManager initialized ({width}x{height}).")

    def is_valid_coordinate(self, x, y):
        """Checks if coordinates are within the grid boundaries."""
        return 0 <= x < self.width and 0 <= y < self.height

    def place_object(self, obj, x, y):
        """Adds an object reference to the grid cell list."""
        if self.is_valid_coordinate(x, y):
            # Ensure grid cell exists (should always with dict init)
            self.grid.setdefault((x,y), []).append(obj)
            logging.debug(f"Placed {obj} at ({x},{y})")
            return True
        logging.warning(f"Failed to place {obj} at invalid coordinate ({x},{y}).")
        return False

    def remove_object(self, obj, x, y):
        """Removes an object reference from the grid cell list."""
        if self.is_valid_coordinate(x, y):
            cell = self.grid.get((x, y), [])
            if obj in cell:
                cell.remove(obj)
                logging.debug(f"Removed {obj} from ({x},{y})")
                return True
        # Log if removal failed (object wasn't where expected)
        # logging.warning(f"Failed to remove {obj} from ({x},{y}) - not found.")
        return False

    def move_object(self, obj, old_x, old_y, new_x, new_y):
        """Moves an object from old coordinates to new coordinates on the grid."""
        if not self.is_valid_coordinate(new_x, new_y):
             logging.warning(f"Invalid move target ({new_x},{new_y}) for {obj}")
             return False # Invalid target coordinate

        removed = self.remove_object(obj, old_x, old_y)
        # Don't necessarily log warning if not found at old pos, could be first placement
        placed = self.place_object(obj, new_x, new_y)

        if placed:
            logging.debug(f"Moved {obj} from ({old_x},{old_y}) to ({new_x},{new_y})")
            # Update agent's internal coordinates AFTER successful grid move
            if isinstance(obj, Agent):
                 obj.x = new_x
                 obj.y = new_y
            return True
        else: # Failed to place at new location
             logging.error(f"Failed to place {obj} at new pos ({new_x},{new_y}) after attempting move from ({old_x},{old_y}).")
             # If it was successfully removed, try to put it back to avoid losing it
             if removed: self.place_object(obj, old_x, old_y)
             return False

    def get_objects_at(self, x, y):
        """Returns the list of objects at a given coordinate."""
        return self.grid.get((x, y), [])

    def get_objects_in_radius(self, x, y, radius):
        """Returns a list of (object, position) tuples within a given radius."""
        objects_in_radius = []
        # Iterate through a square bounding box around the center point
        for i in range(max(0, x - radius), min(self.width, x + radius + 1)):
            for j in range(max(0, y - radius), min(self.height, y + radius + 1)):
                # Check distance using squared comparison for efficiency (circular radius)
                if (x-i)**2 + (y-j)**2 <= radius**2:
                    # Add all objects found in the valid cell within the radius
                    for obj in self.grid.get((i,j), []):
                        objects_in_radius.append((obj, (i, j)))
        return objects_in_radius

    def get_random_empty_cell(self):
        """Finds a random empty cell on the grid."""
        attempts = 0
        max_attempts = self.width * self.height # Avoid infinite loop if grid is full
        while attempts < max_attempts:
            x = random.randint(0, self.width - 1)
            y = random.randint(0, self.height - 1)
            if not self.grid.get((x, y), []): # Cell is empty if list is empty
                return (x, y)
            attempts += 1
        logging.warning("Could not find any empty cell.")
        return None # Indicate failure if no empty cell is found