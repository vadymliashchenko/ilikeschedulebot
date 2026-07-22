from aiogram.fsm.state import State, StatesGroup


class AddGroup(StatesGroup):
    choosing_choreographer = State()
    choosing_style = State()
    choosing_time = State()
    choosing_pattern = State()
    entering_date = State()


class RemoveGroup(StatesGroup):
    choosing_group = State()


class EditGroup(StatesGroup):
    choosing_group = State()
    choosing_field = State()
    choosing_style = State()
    choosing_time = State()


class SubstituteInput(StatesGroup):
    waiting_name = State()
