# Contributing to EufyLife API Integration

Contributions to this project are welcome! Here are some guidelines to help you get started.

## Setting up your development environment

The easiest way to get started is to use the Dev Container feature of Visual Studio Code. This method will create a fully configured development environment with all the dependencies.

### Prerequisites

- [git](https://git-scm.com/)
- [Docker](https://www.docker.com/) or [Podman](https://podman.io/)
- [Visual Studio Code](https://code.visualstudio.com/)
- [Remote - Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) extension for VS Code

### Setup

1. Fork this repository
2. Clone your fork
3. Open the repository in Visual Studio Code
4. When prompted, reopen in container
5. Wait for the container to build and start
6. Run `container install` to install the integration in the development environment

## Development workflow

### Testing your changes

1. Make your changes to the integration code
2. Run `container install` to install your changes
3. Restart Home Assistant to see your changes: `container restart`

### Code quality

We use several tools to ensure code quality:

- [Ruff](https://github.com/astral-sh/ruff) for linting and formatting
- Type hints are encouraged

Run these tools before submitting your PR:

```bash
# Format code
python -m ruff format .

# Lint code
python -m ruff check .
```

### Testing

If you want to add tests (which is highly encouraged), place them in the `tests` directory. Use pytest-homeassistant-custom-component to help with testing custom components.

## Pull Request Guidelines

1. **Create a new branch** for your feature or bug fix
2. **Make your changes** with clear, descriptive commit messages
3. **Test your changes** thoroughly
4. **Update documentation** if needed
5. **Submit a pull request** with a clear description of your changes

### PR Title Format

Use conventional commit format for PR titles:

- `feat: add new feature`
- `fix: resolve bug`
- `docs: update documentation`
- `style: formatting changes`
- `refactor: code refactoring`
- `test: add tests`

## Bug Reports and Feature Requests

Please use the GitHub issue tracker to:

- Report bugs
- Request new features
- Ask questions

When reporting bugs, include:

- Home Assistant version
- Integration version
- Steps to reproduce
- Expected vs actual behavior
- Relevant logs

## Code Guidelines

### Python Style

- Follow PEP 8
- Use type hints
- Add docstrings to public methods
- Keep functions small and focused

### Home Assistant Integration Guidelines

- Follow [Home Assistant development guidelines](https://developers.home-assistant.io/)
- Use the data coordinator pattern for API calls
- Implement proper error handling
- Add appropriate logging
- Use Home Assistant's built-in features (device registry, entity registry, etc.)

## API Development

When working with the EufyLife API:

- Be respectful of rate limits
- Handle API errors gracefully
- Don't hardcode credentials in tests
- Document any new API endpoints discovered

## Questions?

If you have questions about contributing, feel free to:

- Open a discussion on GitHub
- Create an issue for clarification
- Reach out to the maintainers

Thank you for your contributions! 🎉 