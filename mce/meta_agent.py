"""
Meta-agent implementation using the OpenAI-compatible tool-calling agent (mce.agent).

The meta-agent generates and evolves skills for the base-level context learning agent.
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, Any
import os

from mce.agent import ToolAgent, Sandbox
from mce.logging_utils import setup_logger
from mce.prompts.meta_agent import build_meta_agent_prompt
from mce.utils import cleanup_irrelevant_files

from dotenv import load_dotenv

load_dotenv(override=False)


def _verify_meta_agent_outputs(
    iter_dir: Path,
    logger: logging.Logger,
) -> Dict[str, Any]:
    """
    Verify that meta-agent generated required files and read them.
    
    Args:
        iter_dir: Iteration directory
        logger: Logger instance
        
    Returns:
        Dictionary with success status and skill_md
    """
    skills_dir = iter_dir / ".claude" / "skills" / "learning-context"
    skill_file = skills_dir / "SKILL.md"
    
    if not skill_file.exists():
        logger.error(f"Meta-agent did not generate SKILL.md at {skill_file}")
        return {
            'success': False,
            'error': f"Meta-agent did not generate SKILL.md at {skill_file}",
            'skill_md': None,
        }
    
    skill_md = skill_file.read_text()
    
    logger.info(f"✓ Generated SKILL.md ({len(skill_md)} chars)")
    
    return {
        'success': True,
        'skill_md': skill_md,
        'error': None
    }


def build_meta_agent_sandbox(iter_dir: Path, workspace_base: Path) -> Sandbox:
    """
    Sandbox for meta-agent with skill database access.

    The meta-agent can:
    - Read files anywhere in workspace_base (for skill database inspection)
    - Write files ONLY in current iter_dir/.claude/skills/
    """
    iter_dir = Path(iter_dir).resolve()
    return Sandbox(
        cwd=workspace_base,
        read_roots=[workspace_base],
        write_roots=[iter_dir / ".claude" / "skills"],
    )


META_AGENT_TOOLS = ["Read", "Write", "Glob"]


async def run_meta_agent(
    iter_dir: Path,
    task_instruction: str,
    interface_signatures: list,
    iteration: int,
    workspace_base: Path = None,
    run_dir: Path = None,
    e2b_sandbox_manager = None,
) -> Dict[str, Any]:
    """
    Run meta-agent to generate/evolve skills through agentic crossover.
    
    The meta-agent:
    1. Accesses the implicit skill database (workspace history of iterations)
    2. Analyzes previous iterations: (iter_i, design overview, validation acc)
    3. Performs agentic crossover on ideas and strategies
    4. Actively inspects detailed implementations when needed
    5. Generates new skills for the base-level agent
    
    Args:
        iter_dir: Iteration directory
        task_instruction: Task-specific instruction from env
        interface_signatures: List of InterfaceSignature objects
        iteration: Current iteration number
        workspace_base: Base workspace directory
        run_dir: Run directory for organized logging
        e2b_sandbox_manager: E2B sandbox manager (None = run locally)
    """
    workspace_base = Path(iter_dir.parent) if workspace_base is None else Path(workspace_base)
    
    # Setup iteration-specific logger
    logger = setup_logger(
        name=f"meta_iter{iteration}",
        run_dir=run_dir,
        agent_type="meta",
        iteration=iteration,
        minimal_console=True
    )
    
    allowed_tools = META_AGENT_TOOLS
    
    # Build prompt based on execution environment
    if e2b_sandbox_manager:
        # Build prompt with E2B paths
        meta_prompt = build_meta_agent_prompt(
            task_instruction=task_instruction,
            interface_signatures=interface_signatures,
            iter_dir=f"/workspace/{iter_dir.name}",
            workspace_base="/workspace",
        )
    else:
        # Build prompt with local paths
        meta_prompt = build_meta_agent_prompt(
            task_instruction=task_instruction,
            interface_signatures=interface_signatures,
            iter_dir=str(iter_dir),
            workspace_base=str(workspace_base),
        )
    
    # Log the prompt
    logger.info("📝 META-AGENT PROMPT:")
    logger.info(f"\n{meta_prompt}\n")
    
    # Run agent in E2B sandbox if manager is provided
    if e2b_sandbox_manager:
        raise NotImplementedError("E2B sandbox is not implemented")
        logger.info("🔒 Running agent in E2B sandbox")
        try:
            result = await e2b_sandbox_manager.run_agent(
                iter_dir=iter_dir,
                prompt=meta_prompt,
                allowed_tools=allowed_tools,
                timeout=1800,  # 30 minutes
                logger=logger,
            )
            
            if not result["success"]:
                logger.error(f"E2B sandbox execution failed: {result.get('stderr', 'Unknown error')}")
                return {
                    'success': False,
                    'error': f"E2B sandbox execution failed: {result.get('stderr', 'Unknown error')}",
                    'skill_md': None,
                }
            
            logger.info("✓ E2B sandbox execution completed")
            logger.info(f"  stdout: {result['stdout'][:500]}...")  # Log first 500 chars
            
            # Clean up irrelevant files
            cleanup_irrelevant_files(iter_dir, agent_type="meta", logger=logger)
            
            # Verify and read generated files
            return _verify_meta_agent_outputs(iter_dir, logger)

        except Exception as e:
            logger.error(f"E2B sandbox execution failed: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'skill_md': None,
            }
    
    agent = ToolAgent(
        sandbox=build_meta_agent_sandbox(iter_dir, workspace_base),
        tools=META_AGENT_TOOLS,
        logger=logger,
        console_prefix=f"  [meta iter{iteration}]",
    )

    # Run agent with validation loop
    max_validation_attempts = int(os.getenv("MCE_MAX_VALIDATION_ATTEMPTS", "5"))
    next_prompt = meta_prompt

    for attempt in range(max_validation_attempts):
        logger.info(f"\n--- Validation attempt {attempt + 1}/{max_validation_attempts} ---")

        agent_result = await agent.query(next_prompt)
        logger.info(
            f"Meta-agent completed with {agent_result.message_count} messages, "
            f"{agent_result.tool_calls} tool calls ({agent_result.stopped_reason})"
        )

        # Verify SKILL.md was generated
        verification_result = _verify_meta_agent_outputs(iter_dir, logger)

        if verification_result['success']:
            # Clean up irrelevant files
            cleanup_irrelevant_files(iter_dir, agent_type="meta", logger=logger)
            return verification_result

        # SKILL.md not generated - provide feedback
        logger.warning(f"❌ SKILL.md not found at expected location")

        # Check if we have more attempts
        if attempt + 1 >= max_validation_attempts:
            logger.error(f"Max validation attempts ({max_validation_attempts}) exceeded")
            break

        # Feed error back to agent
        skills_dir = iter_dir / ".claude" / "skills" / "learning-context"
        expected_path = skills_dir / "SKILL.md"
        next_prompt = f"""
⚠️ VALIDATION ERROR

Your SKILL.md file was not found at the expected location:
{expected_path}

Please create the SKILL.md file at this EXACT path using the Write tool.

Required:
1. Write to path: {expected_path}
2. Include ## Skill Overview section
3. Provide complete learning methodology

Please create the SKILL.md file now.
"""
        logger.info(f"📤 Sending validation feedback to meta-agent...")

    # Validation failed after all attempts
    cleanup_irrelevant_files(iter_dir, agent_type="meta", logger=logger)
    return {
        'success': False,
        'error': f'Meta-agent failed to generate SKILL.md after {max_validation_attempts} attempts',
        'skill_md': None,
    }
