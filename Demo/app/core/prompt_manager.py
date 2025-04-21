"""
Prompt management module for LLM interactions.
Loads and manages prompt templates from configuration files.
"""
import os
import yaml
from typing import Dict, Any, Optional
from pathlib import Path

from langchain.prompts import PromptTemplate

class PromptManager:
    """
    Manages prompt templates for the application.
    Loads templates from YAML files and provides access to them.
    Implements caching for better performance.
    """
    
    def __init__(self, prompts_dir: Optional[str] = None):
        """
        Initialize the prompt manager.
        
        Args:
            prompts_dir: Directory containing prompt template files (YAML)
                         Defaults to app/prompts if None
        """
        self.prompts_dir = prompts_dir or os.path.join("app", "prompts")
        self._cache = {}  # Cache for loaded templates
        
    def get_prompt_template(self, prompt_name: str) -> PromptTemplate:
        """
        Get a prompt template by name.
        Loads the template from file if not cached.
        
        Args:
            prompt_name: Name of the prompt template (without extension)
            
        Returns:
            PromptTemplate configured according to the YAML file
            
        Raises:
            ValueError: If prompt template file doesn't exist or has invalid format
        """
        # Return from cache if available
        if prompt_name in self._cache:
            return self._cache[prompt_name]
        
        # Construct template path
        template_path = os.path.join(self.prompts_dir, f"{prompt_name}.yaml")
        
        # Check if file exists
        if not os.path.exists(template_path):
            raise ValueError(f"Prompt template file not found: {template_path}")
        
        # Load template from file
        try:
            with open(template_path, 'r') as f:
                template_data = yaml.safe_load(f)
                
            # Validate template data
            if not all(key in template_data for key in ["template", "variables"]):
                raise ValueError(f"Invalid prompt template format in {template_path}")
                
            # Create prompt template
            prompt_template = PromptTemplate(
                input_variables=template_data["variables"],
                template=template_data["template"]
            )
            
            # Cache the template
            self._cache[prompt_name] = prompt_template
            
            return prompt_template
            
        except Exception as e:
            raise ValueError(f"Error loading prompt template {prompt_name}: {str(e)}")
    
    def list_available_prompts(self) -> Dict[str, Dict[str, Any]]:
        """
        List all available prompt templates with their metadata.
        
        Returns:
            Dictionary of prompt names with their version and description
        """
        prompts = {}
        
        # List all YAML files in prompts directory
        for filename in os.listdir(self.prompts_dir):
            if filename.endswith(".yaml"):
                prompt_name = filename[:-5]  # Remove .yaml extension
                
                try:
                    # Load metadata only
                    with open(os.path.join(self.prompts_dir, filename), 'r') as f:
                        data = yaml.safe_load(f)
                        
                    # Extract metadata
                    prompts[prompt_name] = {
                        "version": data.get("version", "N/A"),
                        "description": data.get("description", "")
                    }
                except Exception:
                    # Skip files with errors
                    continue
                    
        return prompts
        
    def reload_prompt(self, prompt_name: str) -> PromptTemplate:
        """
        Force reload a prompt template from file, ignoring cache.
        
        Args:
            prompt_name: Name of the prompt template to reload
            
        Returns:
            Reloaded PromptTemplate
        """
        # Remove from cache if present
        if prompt_name in self._cache:
            del self._cache[prompt_name]
            
        # Load fresh template
        return self.get_prompt_template(prompt_name) 