class FileOperationError(Exception):
    """Base class for file operation errors"""
    pass

class HashingError(FileOperationError):
    """Error during file hashing"""
    pass

class ClassificationError(Exception):
    """Error during file classification"""
    pass

class DatabaseError(Exception):
    """Error during database operations"""
    pass

class ConfigurationError(Exception):
    """Error in configuration"""
    pass