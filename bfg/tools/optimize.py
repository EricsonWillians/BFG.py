#!/usr/bin/env python3
"""Optimization utility for BFG.py."""

import json
import os
import sys
from pathlib import Path

def optimize_config():
    """Apply performance optimizations to config.json."""
    config_path = Path("config.json")
    
    # Load existing config or create new one
    if config_path.exists():
        with open(config_path, 'r') as f:
            config = json.load(f)
        print("✓ Loaded existing config.json")
    else:
        config = {}
        print("✓ Creating new config.json")
    
    # Apply performance optimizations
    optimizations = {
        'animated_background': False,  # Disable for maximum performance
        'performance_mode': True,      # Enable performance mode
    }
    
    print("\n🔧 Applying performance optimizations:")
    for key, value in optimizations.items():
        old_value = config.get(key, "not set")
        config[key] = value
        print(f"  {key}: {old_value} → {value}")
    
    # Save optimized config
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
    
    print(f"\n✓ Optimized config saved to {config_path}")

def set_env_vars():
    """Show environment variables for maximum performance."""
    print("\n🌍 For maximum performance, set these environment variables:")
    env_vars = {
        'BFG_ANIMATION_FPS': '15',
        'BFG_ANTIALIASING': 'false',
        'BFG_SCALING_QUALITY': 'fast',
        'BFG_BACKGROUND_ANIM': 'false',
    }
    
    for var, value in env_vars.items():
        print(f"  export {var}={value}")
    
    print("\nOr run with:")
    env_string = " ".join(f"{k}={v}" for k, v in env_vars.items())
    print(f"  {env_string} uv run bfg")

def check_system():
    """Check system for potential performance issues."""
    print("\n🖥️  System Check:")
    
    try:
        import psutil
        
        # Check available memory
        memory = psutil.virtual_memory()
        memory_gb = memory.total / (1024**3)
        print(f"  RAM: {memory_gb:.1f} GB (Available: {memory.available / (1024**3):.1f} GB)")
        
        if memory.available < 1024**3:  # Less than 1GB available
            print("  ⚠️  Low memory detected - consider closing other applications")
        
        # Check CPU
        cpu_count = psutil.cpu_count()
        print(f"  CPU Cores: {cpu_count}")
        
        if cpu_count < 2:
            print("  ⚠️  Single core CPU - performance mode recommended")
        
    except ImportError:
        print("  Install psutil for detailed system info: pip install psutil")
    
    # Check Python version
    python_version = sys.version_info
    print(f"  Python: {python_version.major}.{python_version.minor}.{python_version.micro}")
    
    if python_version < (3, 7):
        print("  ⚠️  Python 3.7+ recommended for better performance")

def main():
    print("💀 BFG.py - Performance Optimizer 💀")
    print("=" * 50)
    
    if len(sys.argv) > 1 and sys.argv[1] == '--check':
        check_system()
        return
    
    check_system()
    optimize_config()
    set_env_vars()
    
    print("\n🚀 Optimization complete!")
    print("   Run 'uv run bfg' to start with optimized settings")
    print("   Run 'uv run bfg-perf' to test performance")
    print("   Run 'uv run bfg-optimize --check' to check system only")

if __name__ == '__main__':
    main()
