#!/bin/sh
git config core.hooksPath .githooks
chmod +x .githooks/*
echo "✅ Git hooks configured"
