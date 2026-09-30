"""
Base-agent implementation using the OpenAI-compatible tool-calling agent (mce.agent).

The base-agent learns task-specific context from training data using skills
provided by the meta-agent. It implements interfaces defined by InterfaceSignatures.

Key features:
- Multi-turn validation loop: agent runs, system validates, feeds errors back
- Signature-driven: validates against InterfaceSignature definitions
- Sandbox: may read/write only inside its iteration directory (utils/ is read-only)
"""

import os
import asyncio
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from mce.agent import ToolAgent, Sandbox
from mce.logging_utils import setup_logger
from mce.prompts.base_agent import build_base_agent_prompt
from mce.utils import cleanup_irrelevant_files
from mce.validation import validate_interfaces, format_validation_feedback, ValidationResult

from env.base import InterfaceSignature

from dotenv import load_dotenv
load_dotenv(override=True)


def build_base_agent_sandbox(iter_dir: Path) -> Sandbox:
    """Base-agent may read/write only within iter_dir, and never write to utils/."""
    iter_dir = Path(iter_dir).resolve()
    return Sandbox(
        cwd=iter_dir,
        read_roots=[iter_dir],
        write_roots=[iter_dir],
        write_deny=[iter_dir / "utils"],
    )


def get_base_agent_tools() -> List[str]:
    """Tools exposed to the base-agent. Bash can be disabled via MCE_AGENT_ENABLE_BASH=0."""
    tools = ["Read", "Write", "Glob"]
    if os.getenv("MCE_AGENT_ENABLE_BASH", "1").lower() not in ("0", "false", "no"):
        tools.append("Bash")
    return tools


async def run_base_agent(
    iter_dir: Path,
    task_instruction: str,
    interface_signatures: List[InterfaceSignature],
    workspace_base: Path = None,
    log_dir: str = "logs",
    run_dir: Path = None,
    iteration: int = None,
    e2b_sandbox_manager = None,
    initial_prompt: str = None,
    max_validation_attempts: int = None,
) -> Dict[str, Any]:
    """
    Run base-agent with multi-turn validation loop.
    
    Args:
        iter_dir: Iteration directory
        task_instruction: Task-instruction from env
        interface_signatures: Required interface signatures to implement
        workspace_base: Base workspace directory
        log_dir: Directory for log files
        run_dir: Run directory for organized logging
        iteration: Iteration number
        e2b_sandbox_manager: E2B sandbox manager (None = run locally)
        initial_prompt: Optional initial prompt
        max_validation_attempts: Max validation retry attempts

    Returns:
        Dict with success status, interfaces, and metadata
    """
    # Extract sub_iteration from iter_dir path
    sub_iteration = None
    iter_dir_name = Path(iter_dir).name
    if "_sub" in iter_dir_name:
        sub_iteration = int(iter_dir_name.split("_sub")[1])

    if max_validation_attempts is None:
        max_validation_attempts = int(os.getenv("MCE_MAX_VALIDATION_ATTEMPTS", "5"))

    # Set up iteration-specific logger
    if run_dir and iteration is not None:
        logger = setup_logger(
            name=f"base_iter{iteration}_sub{sub_iteration}" if sub_iteration is not None else f"base_iter{iteration}",
            run_dir=run_dir,
            agent_type="base",
            iteration=iteration,
            sub_iteration=sub_iteration,
            minimal_console=True
        )
    else:
        logger = setup_logger(name="base_agent", log_dir=log_dir, console_colors=True)
    
    logger.info(f"\n🤖 BASE-AGENT: Learning context")
    logger.info(f"  Iteration directory: {iter_dir}")
    logger.info(f"  Required interfaces: {[s.name for s in interface_signatures]}")
    
    if workspace_base is None:
        workspace_base = iter_dir.parent
    workspace_base = Path(workspace_base)
    
    # Build prompt with interface signatures
    full_prompt = build_base_agent_prompt(
        task_instruction=task_instruction,
        interface_signatures=interface_signatures,
        iter_dir=str(iter_dir),
        workspace_base=str(workspace_base),
        initial_prompt=initial_prompt,
    )
    
    logger.info("\n" + "="*80)
    logger.info("📝 BASE-AGENT PROMPT")
    logger.info("="*80)
    logger.info(f"\n{full_prompt}\n")
    logger.info("="*80 + "\n")
    
    # Handle E2B sandbox execution
    if e2b_sandbox_manager:
        return await _run_in_e2b(
            e2b_sandbox_manager=e2b_sandbox_manager,
            iter_dir=iter_dir,
            full_prompt=full_prompt,
            interface_signatures=interface_signatures,
            logger=logger,
        )
    
    # Local execution with validation loop
    # The SDK auto-loaded project skills; inline SKILL.md so small models see it without an extra Read
    skill_path = Path(iter_dir) / ".claude" / "skills" / "learning-context" / "SKILL.md"
    if skill_path.exists():
        full_prompt += (
            "\n\n## SKILL.md Content\n\n"
            f"Below is the content of `{skill_path}` (no need to Read it again):\n\n"
            f"{skill_path.read_text(encoding='utf-8')}"
        )

    agent = ToolAgent(
        sandbox=build_base_agent_sandbox(iter_dir),
        tools=get_base_agent_tools(),
        logger=logger,
        console_prefix=f"  [base iter{iteration}]" if iteration is not None else "  [base]",
    )

    # If no interface signatures, skip validation loop
    if not interface_signatures:
        logger.info("No interface signatures required - skipping validation")
        agent_result = await agent.query(full_prompt)
        logger.info(
            f"Agent completed with {agent_result.message_count} messages, "
            f"{agent_result.tool_calls} tool calls ({agent_result.stopped_reason})"
        )
        cleanup_irrelevant_files(iter_dir, agent_type="base", logger=logger)
        return {
            'success': True,
            'interfaces': {},
            'message_count': agent_result.message_count,
            'validation_attempts': 0,
        }

    # Validation loop for environments with interfaces
    validation_result: Optional[ValidationResult] = None
    message_count = 0
    next_prompt = full_prompt
    for attempt in range(max_validation_attempts):
        logger.info(f"\n--- Validation attempt {attempt + 1}/{max_validation_attempts} ---")

        agent_result = await agent.query(next_prompt)
        message_count = agent_result.message_count
        logger.info(
            f"Agent completed with {message_count} messages, "
            f"{agent_result.tool_calls} tool calls ({agent_result.stopped_reason})"
        )

        # Validate interfaces
        validation_result = validate_interfaces(iter_dir, interface_signatures)

        if validation_result.success:
            logger.info(f"✅ All {len(interface_signatures)} interfaces validated successfully")
            cleanup_irrelevant_files(iter_dir, agent_type="base", logger=logger)
            return {
                'success': True,
                'interfaces': validation_result.interfaces,
                'message_count': message_count,
                'validation_attempts': attempt + 1,
            }

        # Log validation errors
        logger.warning(f"❌ Validation failed with {len(validation_result.errors)} errors:")
        for error in validation_result.errors:
            logger.warning(f"  - {error}")

        # Check if we have more attempts
        if attempt + 1 >= max_validation_attempts:
            logger.error(f"Max validation attempts ({max_validation_attempts}) exceeded")
            break

        # Feed errors back to agent for continuation
        next_prompt = format_validation_feedback(validation_result)
        logger.info(f"📤 Sending validation feedback to agent...")

    # Validation failed after all attempts
    cleanup_irrelevant_files(iter_dir, agent_type="base", logger=logger)
    return {
        'success': False,
        'error': f'Validation failed after {max_validation_attempts} attempts',
        'last_errors': validation_result.errors if validation_result else [],
        'message_count': message_count,
    }


async def _run_in_e2b(
    e2b_sandbox_manager,
    iter_dir: Path,
    full_prompt: str,
    interface_signatures: List[InterfaceSignature],
    logger: logging.Logger,
) -> Dict[str, Any]:
    """Run agent in E2B sandbox."""
    raise NotImplementedError("E2B sandbox execution is not implemented yet")

    allowed_tools = get_base_agent_tools()

    logger.info("🔒 Running agent in E2B sandbox")
    try:
        result = await e2b_sandbox_manager.run_agent(
            iter_dir=iter_dir,
            prompt=full_prompt,
            allowed_tools=allowed_tools,
            timeout=1800,
            logger=logger,
        )
        
        if not result["success"]:
            logger.error(f"E2B execution failed: {result.get('stderr', 'Unknown error')}")
            return {
                'success': False,
                'error': f"E2B execution failed: {result.get('stderr', 'Unknown error')}",
                'message_count': 0
            }
        
        logger.info("✓ E2B execution completed")
        
        # Validate after E2B execution
        cleanup_irrelevant_files(iter_dir, agent_type="base", logger=logger)
        validation_result = validate_interfaces(iter_dir, interface_signatures)
        
        if validation_result.success:
            return {
                'success': True,
                'interfaces': validation_result.interfaces,
                'message_count': 0,
            }
        else:
            return {
                'success': False,
                'error': 'Validation failed after E2B execution',
                'last_errors': validation_result.errors,
                'message_count': 0,
            }
            
    except Exception as e:
        logger.error(f"E2B execution failed: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e),
            'message_count': 0
        }


if __name__ == "__main__":
    import argparse
    
    async def main():
        parser = argparse.ArgumentParser(description="Run base-agent to learn context")
        parser.add_argument("iter_dir", type=str, help="Iteration directory")
        parser.add_argument("--env", type=str, required=True, help="Environment name")
        parser.add_argument("--iteration", type=int, default=None, help="Iteration number")
        args = parser.parse_args()
        
        from env.registry import EnvironmentRegistry
        
        env = EnvironmentRegistry.get(args.env)
        task_instruction = env.get_task_instruction()
        interface_signatures = env.get_interface_signatures()
        
        iter_dir = Path(args.iter_dir).resolve()
        
        skill_path = iter_dir / ".claude" / "skills" / "learning-context" / "SKILL.md"
        if not skill_path.exists():
            print(f"✗ SKILL.md not found at {skill_path}")
            print("  Run meta-agent first to generate it.")
            return
        
        result = await run_base_agent(
            iter_dir=iter_dir,
            task_instruction=task_instruction,
            interface_signatures=interface_signatures,
            iteration=args.iteration
        )
        
        if result['success']:
            print(f"\n✓ Base-agent completed successfully")
            print(f"  Validated interfaces: {list(result['interfaces'].keys())}")
        else:
            print(f"\n✗ Base-agent failed: {result['error']}")
    
    asyncio.run(main())
