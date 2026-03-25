import logging

logger = logging.getLogger(__name__)

class PortAllocator:
    """
    Port allocator that manages 100-port blocks starting from PORT_OFFSET.
    The allocation state is shared at the class level across all instances.
    """
    PORT_OFFSET = 5200
    MAX_SLOTS = 1000
    MAX_PORTS_IN_SLOT = 99
    
    # Class-level array where index i holds the number of ports allocated 
    # at PORT_OFFSET + 100 * i + 1
    _allocated_blocks = [0] * MAX_SLOTS

    def __init__(self):
        pass

    def alloc(self, number_of_ports, base_port=None):
        """
        Allocates a 100-port block. If base_port is specified, tries to allocate 
        that specific block. Otherwise finds the first available block.
        Returns the base port if successful, None otherwise.
        """
        if number_of_ports > PortAllocator.MAX_PORTS_IN_SLOT:
            logger.error(f"Cannot allocate {number_of_ports} ports: exceeds max slot size {PortAllocator.MAX_PORTS_IN_SLOT}")
            return None

        if base_port:
            if self.is_free(base_port):
                idx = self._get_idx(base_port)
                PortAllocator._allocated_blocks[idx] = number_of_ports
                logger.info(f"Reserved specific base port {base_port} (slot index {idx}) for {number_of_ports} ports")
                return base_port
            return None

        for idx, count in enumerate(PortAllocator._allocated_blocks):
            if count == 0:
                PortAllocator._allocated_blocks[idx] = number_of_ports
                base_port = PortAllocator.PORT_OFFSET + 100 * idx + 1
                logger.debug(f"Allocated block index {idx} with base port {base_port} for {number_of_ports} ports")
                return base_port
        
        logger.warning("No free port blocks available for allocation")
        return None

    def _get_idx(self, base_port):
        """
        Calculates the index in the allocated_blocks array from a base_port.
        """
        return (base_port - PortAllocator.PORT_OFFSET) // 100

    def _min_slot_port(self, idx):
      PORT_OFFSET + ((MAX_SLOTS+1) * idx)

    def _max_slot_port(self, idx):
      PORT_OFFSET + ((MAX_SLOTS+1) * idx) + MAX_SLOTS

    def free(self, base_port):
        """
        Frees the 100-port block starting at base_port.
        """
        idx = self._get_idx(base_port)
        if 0 <= idx < len(PortAllocator._allocated_blocks):
            if PortAllocator._allocated_blocks[idx] != 0:
                logger.info(f"Freeing port block starting at {base_port} (slot index {idx})")
                PortAllocator._allocated_blocks[idx] = 0
            else:
                logger.warning(f"Attempted to free already free port block at {base_port}")

    def is_free(self, base_port):
        """
        Checks if the 100-port block starting at base_port is free.
        """
        idx = self._get_idx(base_port)
        if 0 <= idx < len(PortAllocator._allocated_blocks):
            return PortAllocator._allocated_blocks[idx] == 0
        return False
