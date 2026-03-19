# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import traceback
import datetime
from enum import Enum
from typing import Optional, Dict, Any, Callable

logger = logging.getLogger(__name__)

class ErrorSeverity(Enum):
    """Enum for error severity levels."""
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

class SupervisorAgentError(Exception):
    """Base exception class for Supervisor Agent errors."""
    
    def __init__(
        self, 
        message: str, 
        severity: ErrorSeverity = ErrorSeverity.ERROR,
        details: Optional[Dict[str, Any]] = None,
        original_exception: Optional[Exception] = None
    ):
        self.message = message
        self.severity = severity
        self.details = details or {}
        self.original_exception = original_exception
        
        # Add traceback information if there's an original exception
        if original_exception:
            self.details["traceback"] = traceback.format_exception(
                type(original_exception), 
                original_exception, 
                original_exception.__traceback__
            )
        
        super().__init__(self.message)

class ToolError(SupervisorAgentError):
    """Exception raised for errors in tool execution."""
    
    def __init__(
        self, 
        message: str, 
        tool_name: str,
        tool_args: Optional[Dict[str, Any]] = None,
        **kwargs
    ):
        details = kwargs.pop("details", {})
        details.update({
            "tool_name": tool_name,
            "tool_args": tool_args or {}
        })
        super().__init__(message, details=details, **kwargs)

class RemoteAgentError(SupervisorAgentError):
    """Exception raised for errors in remote agent communication."""
    
    def __init__(
        self, 
        message: str, 
        agent_name: str,
        **kwargs
    ):
        details = kwargs.pop("details", {})
        details.update({
            "agent_name": agent_name
        })
        super().__init__(message, details=details, **kwargs)

class AuthenticationError(SupervisorAgentError):
    """Exception raised for authentication errors."""
    pass

async def send_error_message(sio_sessions, error: SupervisorAgentError):
    """
    Send an error message to the user through the socket.
    
    Args:
        sio_sessions: Dictionary of socket sessions
        error: The error that occurred
    """
    # Format the error message with severity
    error_message = f"[{error.severity.value}] {error.message}"
    
    # Add details if available
    if error.details:
        error_details = "\n\nDetails:\n"
        for key, value in error.details.items():
            if key == "traceback":
                # Don't include traceback in user-facing messages
                continue
            else:
                error_details += f"\n{key}: {value}"
        error_message += error_details
    
    # Generate a unique message ID for this error
    import uuid
    message_id = str(uuid.uuid4())
    
    # Send proper AG-UI events: START -> CONTENT -> END
    start_event = {
        'type': 'TEXT_MESSAGE_START',
        'timestamp': None,
        'raw_event': None,
        'message_id': message_id,
        'role': 'assistant'
    }
    
    content_event = {
        'type': 'TEXT_MESSAGE_CONTENT',
        'timestamp': None,
        'raw_event': None,
        'message_id': message_id,
        'delta': error_message
    }
    
    end_event = {
        'type': 'TEXT_MESSAGE_END',
        'timestamp': None,
        'raw_event': None,
        'message_id': message_id
    }

    # Send AG-UI events to all registered sessions
    for sid, sio in sio_sessions.items():
        await sio.emit('agui_event', start_event, room=sid)
        await sio.emit('agui_event', content_event, room=sid)
        await sio.emit('agui_event', end_event, room=sid)
        logger.info(f"Sent error message to {sid}: {error_message}")

def with_error_handling(error_handler: Callable):
    """
    Decorator for handling exceptions in async functions.
    
    This decorator catches exceptions, logs them, and calls the provided
    error handler function to handle the error (e.g., send a message to the user).
    
    Args:
        error_handler: A callable that takes an exception and handles it
        
    Returns:
        A wrapped function that handles exceptions
    """
    import functools
    
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except SupervisorAgentError as e:
                # Log the error with appropriate severity
                if e.severity == ErrorSeverity.INFO:
                    logger.info(f"SupervisorAgentError: {e.message}", exc_info=True)
                elif e.severity == ErrorSeverity.WARNING:
                    logger.warning(f"SupervisorAgentError: {e.message}", exc_info=True)
                elif e.severity == ErrorSeverity.ERROR:
                    logger.error(f"SupervisorAgentError: {e.message}", exc_info=True)
                elif e.severity == ErrorSeverity.CRITICAL:
                    logger.critical(f"SupervisorAgentError: {e.message}", exc_info=True)
                
                # Call the error handler
                await error_handler(e)
                
                # Re-raise the error to be handled by the caller
                raise
            except Exception as e:
                # Convert generic exceptions to SupervisorAgentError
                error = SupervisorAgentError(
                    message=f"Unexpected error: {str(e)}",
                    severity=ErrorSeverity.ERROR,
                    original_exception=e
                )
                logger.error(f"Unexpected error: {str(e)}", exc_info=True)
                
                # Call the error handler
                await error_handler(error)
                
                # Re-raise as SupervisorAgentError
                raise error
        
        return wrapper
    
    return decorator
