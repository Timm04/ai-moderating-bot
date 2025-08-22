# AI Moderation Bot

This repository contains the backend service for an **AI-powered Discord moderation bot** designed to help communities manage conversations more effectively.  
The bot leverages **semantic embeddings** and **moderator feedback** to create **adaptive, server-specific moderation**, capable of understanding custom rules, slang, and memes.  

The system is built with **Python** and **Discord.py**, providing a modular and scalable architecture for multi-server support.  


## Core Features

- **AI-Powered Moderation**  
  Uses OpenAI embeddings to evaluate messages against server-defined rules, flagging potential violations based on **semantic similarity** rather than simple keyword matching.  

- **Custom Rule Management**  
  Admins can define rules such as *“no sarcasm”* or *“no NSFW memes”*. Rules are embedded into vector space for flexible and context-aware matching.  

- **Feedback Loop Learning**  
  Human moderator approvals/rejections are stored, allowing the bot to adjust similarity thresholds and gradually align with each community’s culture.  

- **Review Workflow UI**  
  Flagged messages are sent to a moderation review channel with **interactive buttons and dropdowns**, making it easy for moderators to approve, reject, or reassign rules.  

- **Scalable Multi-Server Support**  
  Each server has isolated configurations (rules, thresholds, moderator role, review channels) stored in PostgreSQL.  

- **Efficient Storage & Caching**  
  PostgreSQL with SQLAlchemy for persistence, Redis for caching and embedding normalization.  

- **Deployment Ready**  
  Containerized with Docker & Docker Compose, with support for GitHub Actions CI/CD pipelines for automated testing and deployment.  


## Technology Stack

| **Category**         | **Technology**                                                                 |
|----------------------|---------------------------------------------------------------------------------|
| Framework            | Discord.py (commands & app_commands, discord.ui)                               |
| Language             | Python 3.11                                                                     |
| Database             | PostgreSQL (async via asyncpg), SQLAlchemy ORM, Alembic migrations             |
| ORM                  | SQLAlchemy async ORM                                                            |
| AI Model Type        | Embedding-based semantic similarity                                             |
| AI Services          | OpenAI embeddings (`text-embedding-3-small`) via async API                      |
| AI Usage             | Embed messages & rules → cosine similarity → flag if above threshold           |
| Learning Loop        | Moderator feedback (approve/reject) updates server-specific thresholds          |
| Caching              | Redis (for rule/message cache, embedding normalization)                        |
| Containerization     | Docker, Docker Compose                                                          |
| Task Queue / Async   | asyncio (built-in), Discord event loop                                          |
| Deployment / CI/CD   | GitHub Actions (tests + auto-deploy via Docker)                                 |
| Package Manager      | pip / requirements.txt                                                          |
| Authentication       | Discord bot token (env-based)                                                   |
| Config Management    | Environment variables (.env, Docker Compose)                                    |

